"""City cover images from Wikimedia Commons, chosen through Wikidata.

Source (no key; Wikimedia API etiquette: a descriptive User-Agent with a contact URL, one
request at a time, maxlag on Wikidata, retries with backoff):
    wikidata  GET https://www.wikidata.org/w/api.php?action=wbgetclaims&entity=<Q>&property=P18
              the city's "image" statements (P18) with their rank
    commons   GET https://commons.wikimedia.org/w/api.php?action=query&prop=imageinfo
              &iiprop=url|size|mime|extmetadata&iiurlwidth=1280&titles=File:A|File:B
              file page URL, original and 1280 px thumbnail URLs and sizes, and the licence,
              artist and credit metadata (extmetadata)

Per city (config/cities.yaml: wikidata_id, optional cover_image_file):
    candidates  cover_image_file if set (an override), otherwise the item's P18 files,
                preferred rank first, then normal; deprecated statements are ignored
    selection   the first candidate whose licence is in the allow-list (CC0, public domain,
                CC BY, CC BY-SA). If none is allowed, the first candidate is kept with
                license_family "other", so the dbt tests fail and name the file; an override
                is never replaced by another file.

Layers:
    raw     s3://raw/wikimedia/wikidata/snapshot_date=YYYY-MM-DD/<city_id>.json  (P18 claims)
            s3://raw/wikimedia/commons/snapshot_date=YYYY-MM-DD/<city_id>.json   (imageinfo)
    bronze  iceberg.bronze.wikimedia_city_images: one row per city and snapshot_date, the
            selected file with its metadata as received (HTML kept; cleaned in silver).

Every image keeps its own licence: the website shows the author, the licence with its link and
"via Wikimedia Commons" wherever the image appears (ADR-0011).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pyarrow as pa
import requests
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    IntegerType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion import __version__
from ingestion.common.http import TryLaterError, build_session
from ingestion.common.iceberg import ensure_table, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_json, s3_client

log = get_logger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia User-Agent policy: identify the client and give a way to reach its operator.
USER_AGENT = (
    f"WanderlakeBot/{__version__} (+https://github.com/gunaybahmanli/wanderlake; "
    "weekly city cover images)"
)
THUMB_WIDTH = 1280
REQUEST_PAUSE_SECONDS = 1.0  # about one request per second, one at a time
# Wikidata answers "maxlag" while its replicas lag behind: wait (Retry-After, at least 5 s, at
# most 60 s) and ask again, a few times; then give up politely until the next run.
MAXLAG_ATTEMPTS = 5
MAXLAG_MAX_WAIT_SECONDS = 60.0
PREFIX_TEMPLATE = "wikimedia/{kind}/snapshot_date={date}/"
RAW_BUCKET = "raw"
BRONZE_TABLE = "bronze.wikimedia_city_images"
EXTMETADATA_FIELDS = (
    "LicenseShortName|License|LicenseUrl|Artist|Credit|AttributionRequired|UsageTerms"
    "|Copyrighted|ImageDescription"
)

ALLOWED_LICENSE_FAMILIES = ("cc0", "public-domain", "cc-by", "cc-by-sa")

BRONZE_SCHEMA = Schema(
    NestedField(1, "snapshot_date", DateType(), required=True),
    NestedField(2, "city_id", StringType(), required=True),
    NestedField(3, "wikidata_id", StringType(), required=True),
    NestedField(4, "file_name", StringType(), required=False, doc="Commons file, no File:"),
    NestedField(5, "selection", StringType(), required=True, doc="preferred|normal|override|none"),
    NestedField(
        6, "candidates_json", StringType(), required=True, doc="Files considered, in order"
    ),
    NestedField(7, "file_page_url", StringType(), required=False),
    NestedField(8, "original_url", StringType(), required=False),
    NestedField(9, "original_width", IntegerType(), required=False),
    NestedField(10, "original_height", IntegerType(), required=False),
    NestedField(11, "thumb_url", StringType(), required=False, doc="About 1280 px wide"),
    NestedField(12, "thumb_width", IntegerType(), required=False),
    NestedField(13, "thumb_height", IntegerType(), required=False),
    NestedField(14, "mime", StringType(), required=False),
    NestedField(15, "license_short_name", StringType(), required=False, doc="e.g. CC BY-SA 4.0"),
    NestedField(16, "license_code", StringType(), required=False, doc="e.g. cc-by-sa-4.0"),
    NestedField(17, "license_url", StringType(), required=False),
    NestedField(
        18,
        "license_family",
        StringType(),
        required=True,
        doc="cc0|public-domain|cc-by|cc-by-sa|other",
    ),
    NestedField(19, "artist_html", StringType(), required=False, doc="As received (HTML)"),
    NestedField(20, "credit_html", StringType(), required=False, doc="As received (HTML)"),
    NestedField(21, "attribution_required", BooleanType(), required=False),
    NestedField(22, "usage_terms", StringType(), required=False),
    NestedField(23, "_ingested_at", TimestamptzType(), required=True),
    NestedField(24, "_loaded_at", TimestamptzType(), required=True),
    NestedField(25, "_source_file", StringType(), required=True),
    NestedField(26, "_dag_run_id", StringType(), required=False),
)

# Yearly partitions: 10 rows a week.
BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=YearTransform(), name="snapshot_year")
)


class WikidataLagError(TryLaterError):
    """Wikidata kept answering maxlag: try again on the next run."""


class WikimediaError(RuntimeError):
    """Raised when a response is unusable or raw files are missing."""


# --- licences -----------------------------------------------------------------------------------

_CC_NAME = re.compile(r"(?i)cc[ -]by(?P<sa>[ -]sa)?[ -]\d\.\d(?:[ -][a-z]{2,3})?(?:[ -]igo)?")
_CC_CODE = re.compile(r"(?i)cc-by(?P<sa>-sa)?-\d\.\d(?:-[a-z]{2,3})?(?:-igo)?")


def license_family(short_name: str | None, license_code: str | None) -> str:
    """Classify a Commons licence: cc0, public-domain, cc-by, cc-by-sa, or other.

    Uses the machine-readable code (extmetadata "License", e.g. cc-by-sa-4.0) when present,
    else the short name (e.g. "CC BY-SA 4.0"). Non-commercial (NC) and no-derivatives (ND)
    variants, GFDL-only, "Attribution" (the Commons attribution-only template) and anything
    unknown are "other", which the dbt tests reject.
    """
    code = (license_code or "").strip().lower()
    name = (short_name or "").strip()
    if re.search(r"(?i)(^|[ -])n[cd]([ -]|$)", f"{code} {name}"):
        return "other"
    if code in {"cc0", "cc0-1.0"} or re.fullmatch(r"(?i)cc0(?: 1\.0)?", name):
        return "cc0"
    if (
        code == "pd"
        or code.startswith("pd-")
        or re.fullmatch(r"(?i)public domain|pd(?:[ -].*)?", name)
    ):
        return "public-domain"
    for match in (_CC_CODE.fullmatch(code), _CC_NAME.fullmatch(name)):
        if match:
            return "cc-by-sa" if match["sa"] else "cc-by"
    return "other"


# --- raw: Wikidata and Commons ------------------------------------------------------------------


def build_wikimedia_session() -> requests.Session:
    session = build_session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def p18_candidates(claims_doc: dict[str, Any]) -> list[dict[str, str]]:
    """P18 files in selection order: preferred rank first, then normal; deprecated ignored."""
    if "error" in claims_doc:
        raise WikimediaError(f"Wikidata error: {claims_doc['error']}")
    files = []
    for claim in claims_doc.get("claims", {}).get("P18", []):
        rank = claim.get("rank", "normal")
        value = (claim.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if rank != "deprecated" and isinstance(value, str) and value:
            files.append({"file": value, "rank": rank})
    order = {"preferred": 0, "normal": 1}
    return sorted(files, key=lambda f: order.get(f["rank"], 2))  # stable: keeps Wikidata order


def get_claims(
    session: requests.Session,
    wikidata_id: str,
    attempts: int = MAXLAG_ATTEMPTS,
    sleep: Any | None = None,
) -> requests.Response:
    """wbgetclaims for P18 with maxlag=5. On a maxlag answer, wait as Wikidata asks and retry;
    after `attempts` answers raise WikidataLagError. Only a real answer is returned (and stored)."""
    for attempt in range(1, attempts + 1):
        response = session.get(
            WIKIDATA_API,
            params={
                "action": "wbgetclaims",
                "entity": wikidata_id,
                "property": "P18",
                "format": "json",
                "maxlag": "5",
            },
        )
        response.raise_for_status()
        error = response.json().get("error") or {}
        if error.get("code") != "maxlag":
            return response
        try:
            wait = float(response.headers.get("Retry-After") or 5)
        except ValueError:
            wait = 5.0
        wait = min(max(wait, 5.0) * attempt, MAXLAG_MAX_WAIT_SECONDS)
        log_event(
            log, "wikimedia.maxlag", wikidata_id=wikidata_id, lag=error.get("lag"), attempt=attempt
        )
        if attempt < attempts:
            (sleep or time.sleep)(wait)
    raise WikidataLagError(
        f"Wikidata is lagging (maxlag) after {attempts} attempts; try again on the next run"
    )


def ingest_snapshot(
    snapshot_date: date,
    bucket: str = RAW_BUCKET,
    cities: Sequence[Any] | None = None,
    session: requests.Session | None = None,
    s3: Any | None = None,
    pause_seconds: float = REQUEST_PAUSE_SECONDS,
) -> dict[str, Any]:
    """Fetch the P18 claims and the Commons metadata of every candidate; store both unchanged."""
    if cities is None:
        from ingestion.config import load_cities

        cities = load_cities()
    session = session or build_wikimedia_session()
    s3 = s3 or s3_client()
    ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
    common = {"ingested_at": ingested_at, "snapshot_date": snapshot_date.isoformat()}
    wikidata_prefix = PREFIX_TEMPLATE.format(kind="wikidata", date=snapshot_date.isoformat())
    commons_prefix = PREFIX_TEMPLATE.format(kind="commons", date=snapshot_date.isoformat())
    files = 0
    for i, city in enumerate(cities):
        if i and pause_seconds:
            time.sleep(pause_seconds)
        claims = get_claims(session, city.wikidata_id)
        put_json(
            bucket,
            f"{wikidata_prefix}{city.id}.json",
            claims.content,
            {
                **common,
                "source_url": claims.url,
                "http_status": str(claims.status_code),
                "city_id": city.id,
                "wikidata_id": city.wikidata_id,
            },
            s3,
        )
        override = getattr(city, "cover_image_file", None)
        titles = [override] if override else [c["file"] for c in p18_candidates(claims.json())]
        if not titles:
            log_event(log, "wikimedia.no_image", city_id=city.id, wikidata_id=city.wikidata_id)
            continue
        if pause_seconds:
            time.sleep(pause_seconds)
        info = session.get(
            COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": str(THUMB_WIDTH),
                "iiextmetadatafilter": EXTMETADATA_FIELDS,
                "titles": "|".join(f"File:{t}" for t in titles),
            },
        )
        info.raise_for_status()
        put_json(
            bucket,
            f"{commons_prefix}{city.id}.json",
            info.content,
            {
                **common,
                "source_url": info.url[:1500],
                "http_status": str(info.status_code),
                "city_id": city.id,
                "override_file": override or "",
            },
            s3,
        )
        files += 1
    result = {
        "bucket": bucket,
        "snapshot_date": snapshot_date.isoformat(),
        "cities": len(cities),
        "with_candidates": files,
    }
    log_event(log, "wikimedia.ingested", **result)
    return result


# --- raw -> bronze ------------------------------------------------------------------------------


def _norm(title: str) -> str:
    """Commons title normalisation: no "File:", underscores are spaces, first letter upper."""
    name = title.removeprefix("File:").replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def canonical_media_url(url: str | None) -> str | None:
    """Commons media URL on upload.wikimedia.org, without query string.

    The API may return thumbnails on thumb.wikimedia.org with tracking parameters (utm_*);
    the same path on upload.wikimedia.org serves the same file. One host keeps the website's
    image allow-list (next.config.ts) to a single domain.
    """
    if not url:
        return None
    parts = urlsplit(url)
    host = (
        "upload.wikimedia.org"
        if parts.hostname in {"thumb.wikimedia.org", "upload.wikimedia.org"}
        else parts.netloc
    )
    return urlunsplit(("https", host, parts.path, "", ""))


def _meta(extmetadata: dict[str, Any], key: str) -> str | None:
    value = (extmetadata.get(key) or {}).get("value")
    return str(value).strip() if value not in (None, "") else None


def image_metadata(commons_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{normalised file name: metadata} from a Commons imageinfo response (formatversion 2)."""
    pages = (commons_doc.get("query") or {}).get("pages") or []
    out = {}
    for page in pages:
        if page.get("missing") or not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        md = info.get("extmetadata") or {}
        short, code = _meta(md, "LicenseShortName"), _meta(md, "License")
        attribution = _meta(md, "AttributionRequired")
        out[_norm(page["title"])] = {
            "file_page_url": info.get("descriptionurl"),
            "original_url": canonical_media_url(info.get("url")),
            "original_width": info.get("width"),
            "original_height": info.get("height"),
            "thumb_url": canonical_media_url(info.get("thumburl")),
            "thumb_width": info.get("thumbwidth"),
            "thumb_height": info.get("thumbheight"),
            "mime": info.get("mime"),
            "license_short_name": short,
            "license_code": code,
            "license_url": _meta(md, "LicenseUrl"),
            "license_family": license_family(short, code),
            "artist_html": _meta(md, "Artist"),
            "credit_html": _meta(md, "Credit"),
            "attribution_required": None if attribution is None else attribution.lower() == "true",
            "usage_terms": _meta(md, "UsageTerms"),
        }
    return out


EMPTY_IMAGE = {f.name: None for f in BRONZE_SCHEMA.fields if f.field_id in range(7, 23)}


def select_image(
    candidates: list[dict[str, str]], metadata: dict[str, dict[str, Any]], override: str | None
) -> dict[str, Any]:
    """The image to use: the override, or the first candidate with an allowed licence.

    Returns file_name, selection and the metadata fields. Without an allowed candidate, the first
    candidate is returned (license_family "other") so the data check fails visibly.
    """
    if override:
        name = _norm(override)
        md = metadata.get(name)
        if md is None:
            raise WikimediaError(f"cover_image_file {override!r} was not found on Commons")
        return {"file_name": name, "selection": "override", **md}
    usable = [(c, metadata.get(_norm(c["file"]))) for c in candidates]
    usable = [(c, md) for c, md in usable if md is not None]
    for c, md in usable:
        if md["license_family"] in ALLOWED_LICENSE_FAMILIES:
            return {"file_name": _norm(c["file"]), "selection": c["rank"], **md}
    if usable:
        c, md = usable[0]
        return {"file_name": _norm(c["file"]), "selection": c["rank"], **md}
    return {"file_name": None, "selection": "none", **EMPTY_IMAGE, "license_family": "other"}


def load_bronze(
    snapshot_date: date,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    cities: Sequence[Any] | None = None,
    catalog: Any | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    """Load one snapshot into bronze, replacing any earlier load of the same snapshot_date."""
    if cities is None:
        from ingestion.config import load_cities

        cities = load_cities()
    s3 = s3 or s3_client()
    day = snapshot_date.isoformat()
    loaded_at = datetime.now(UTC)
    rows = []
    for city in cities:
        wd_key = f"{PREFIX_TEMPLATE.format(kind='wikidata', date=day)}{city.id}.json"
        try:
            wd_obj = s3.get_object(Bucket=bucket, Key=wd_key)
        except s3.exceptions.NoSuchKey as exc:
            raise WikimediaError(
                f"no raw Wikidata claims for {city.id}: s3://{bucket}/{wd_key}"
            ) from exc
        wd_meta = {k.lower(): v for k, v in wd_obj.get("Metadata", {}).items()}
        candidates = p18_candidates(json.loads(wd_obj["Body"].read()))
        cm_key = f"{PREFIX_TEMPLATE.format(kind='commons', date=day)}{city.id}.json"
        override, metadata, source = None, {}, f"s3://{bucket}/{wd_key}"
        try:
            cm_obj = s3.get_object(Bucket=bucket, Key=cm_key)
            override = {k.lower(): v for k, v in cm_obj.get("Metadata", {}).items()}.get(
                "override_file"
            ) or None
            metadata = image_metadata(json.loads(cm_obj["Body"].read()))
            source = f"s3://{bucket}/{cm_key}"
        except s3.exceptions.NoSuchKey:
            pass  # no candidates: the city gets selection "none"
        image = select_image(candidates, metadata, override)
        considered = [{"file": override, "rank": "override"}] if override else candidates
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "city_id": city.id,
                "wikidata_id": city.wikidata_id,
                **image,
                "candidates_json": json.dumps(considered, ensure_ascii=False),
                "_ingested_at": datetime.fromisoformat(wd_meta["ingested_at"]),
                "_loaded_at": loaded_at,
                "_source_file": source,
                "_dag_run_id": dag_run_id,
            }
        )
    batch = pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA.as_arrow())
    table = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    written = replace_rows(table, batch, "snapshot_date", day)
    not_allowed = [
        r["city_id"] for r in rows if r["license_family"] not in ALLOWED_LICENSE_FAMILIES
    ]
    result = {
        "table": BRONZE_TABLE,
        "snapshot_date": day,
        "rows": written,
        "without_open_licence": ",".join(not_allowed) or "none",
    }
    log_event(log, "wikimedia.bronze_loaded", **result)
    return result
