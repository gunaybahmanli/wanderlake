"""UK FCDO foreign travel advice (GOV.UK Content API) for the countries of our cities.

Source (no key; at most 10 requests per second per client):
    index    GET https://www.gov.uk/api/content/foreign-travel-advice
             links.children[]: details.country {name, slug, synonyms}, public_updated_at, ...
    country  GET https://www.gov.uk/api/content/foreign-travel-advice/<slug>
             title, public_updated_at, details {alert_status, change_description, parts[...]}

Country mapping (no hand-typed list): GOV.UK has no ISO codes, so each country_code in
config/cities.yaml is resolved through official names: ISO code -> name_common/name_official in
the latest bronze.rest_countries snapshot -> the FCDO index (country name and its synonyms). A
code that matches no slug, or more than one (FCDO synonyms include city names), fails the run.

Layers:
    raw     s3://raw/fcdo/advice/snapshot_date=YYYY-MM-DD/<country_code>.json  (unchanged)
            s3://raw/fcdo/advice/snapshot_date=YYYY-MM-DD/_index.json         (mapping source)
    bronze  iceberg.bronze.fcdo_advice: one row per country and snapshot_date. The HTML of each
            section stays unchanged (parts_json); cleaning to text happens in silver, summaries
            in phase 2.

License: Open Government Licence v3.0. Contains public sector information licensed under the
Open Government Licence v3.0.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import requests
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import DateType, NestedField, StringType, TimestamptzType

from ingestion.common.http import build_session
from ingestion.common.iceberg import ensure_table, get_catalog, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_json, s3_client

log = get_logger(__name__)

INDEX_URL = "https://www.gov.uk/api/content/foreign-travel-advice"
COUNTRY_URL = "https://www.gov.uk/api/content/foreign-travel-advice/{slug}"
PREFIX_TEMPLATE = "fcdo/advice/snapshot_date={date}/"
INDEX_FILE = "_index.json"
RAW_BUCKET = "raw"
BRONZE_TABLE = "bronze.fcdo_advice"
COUNTRIES_TABLE = "bronze.rest_countries"
REQUEST_PAUSE_SECONDS = 1.0  # far below the documented 10 requests per second

BRONZE_SCHEMA = Schema(
    NestedField(1, "snapshot_date", DateType(), required=True),
    NestedField(2, "country_code", StringType(), required=True, doc="ISO 3166-1 alpha-2"),
    NestedField(3, "slug", StringType(), required=True, doc="FCDO page slug"),
    NestedField(4, "title", StringType(), required=True),
    NestedField(5, "public_updated_at", TimestamptzType(), required=False),
    NestedField(6, "change_description", StringType(), required=False),
    NestedField(7, "alert_status_json", StringType(), required=True, doc="List, as received"),
    NestedField(8, "parts_json", StringType(), required=True, doc="[{slug, title, body (HTML)}]"),
    NestedField(9, "_ingested_at", TimestamptzType(), required=True),
    NestedField(10, "_loaded_at", TimestamptzType(), required=True),
    NestedField(11, "_source_file", StringType(), required=True),
    NestedField(12, "_dag_run_id", StringType(), required=False),
)

# Yearly partitions: 10 rows a day (about 1 MB of HTML), so about 3,650 rows a year.
BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=YearTransform(), name="snapshot_year")
)


class FcdoError(RuntimeError):
    """Raised when a response is unusable or a country cannot be resolved."""


# --- country mapping ------------------------------------------------------------------------------


def normalize(name: str) -> str:
    """Lower case, no accents or punctuation, no leading 'the' (e.g. 'The Netherlands')."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_name.lower()).strip().removeprefix("the ")


def index_countries(index: dict[str, Any]) -> list[dict[str, Any]]:
    children = (index.get("links") or {}).get("children")
    if index.get("document_type") != "travel_advice_index" or not isinstance(children, list):
        raise FcdoError("not a travel advice index")
    return [c["details"]["country"] for c in children if (c.get("details") or {}).get("country")]


def resolve_slugs(
    country_names: Mapping[str, Sequence[str]], index: dict[str, Any]
) -> dict[str, str]:
    """Map ISO codes to FCDO slugs via official names. Fails if any code has 0 or 2+ matches."""
    by_name: dict[str, set[str]] = {}
    for country in index_countries(index):
        for name in [country["name"], *(country.get("synonyms") or [])]:
            by_name.setdefault(normalize(name), set()).add(country["slug"])

    resolved: dict[str, str] = {}
    problems: list[str] = []
    for code, names in sorted(country_names.items()):
        slugs: set[str] = set()
        for name in names:
            slugs |= by_name.get(normalize(name), set())
        if len(slugs) == 1:
            resolved[code] = slugs.pop()
        elif not slugs:
            problems.append(f"{code} ({', '.join(names)}): no FCDO country")
        else:
            problems.append(f"{code} ({', '.join(names)}): ambiguous {sorted(slugs)}")
    if problems:
        log_event(log, "fcdo.mapping_failed", problems="; ".join(problems))
        raise FcdoError("cannot map countries to FCDO pages: " + "; ".join(problems))
    log_event(log, "fcdo.mapping_ok", mapping=",".join(f"{c}={s}" for c, s in resolved.items()))
    return resolved


def country_names_from_bronze(codes: set[str], catalog: Any | None = None) -> dict[str, list[str]]:
    """name_common and name_official per code from the latest bronze.rest_countries snapshot."""
    from pyiceberg.expressions import EqualTo

    catalog = catalog or get_catalog()
    if not catalog.table_exists(COUNTRIES_TABLE):
        raise FcdoError(f"{COUNTRIES_TABLE} does not exist yet: run the rest_countries DAG first")
    table = catalog.load_table(COUNTRIES_TABLE)
    dates = table.scan(selected_fields=("snapshot_date",)).to_arrow()["snapshot_date"]
    if len(dates) == 0:
        raise FcdoError(f"{COUNTRIES_TABLE} is empty: run the rest_countries DAG first")
    latest = max(dates.to_pylist())
    rows = (
        table.scan(
            row_filter=EqualTo("snapshot_date", latest.isoformat()),
            selected_fields=("cca2", "name_common", "name_official"),
        )
        .to_arrow()
        .to_pylist()
    )
    names = {
        r["cca2"]: [n for n in (r["name_common"], r["name_official"]) if n]
        for r in rows
        if r["cca2"] in codes
    }
    missing = sorted(codes - set(names))
    if missing:
        raise FcdoError(f"not in {COUNTRIES_TABLE} ({latest}): {missing}")
    return names


# --- raw ----------------------------------------------------------------------------------------


def parse_advice(body: bytes) -> dict[str, Any]:
    """Validate one country response; returns the parsed JSON."""
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise FcdoError("response is not valid JSON") from exc
    details = doc.get("details") if isinstance(doc, dict) else None
    if doc.get("document_type") != "travel_advice" or not isinstance(details, dict):
        raise FcdoError(f"not a travel advice page: {doc.get('document_type')!r}")
    parts = details.get("parts")
    if not isinstance(parts, list) or not parts:
        raise FcdoError(f"{doc.get('title')}: no parts")
    for part in parts:
        if not {"slug", "title", "body"} <= set(part):
            raise FcdoError(f"{doc.get('title')}: part without slug/title/body")
    return doc


def ingest_snapshot(
    snapshot_date: date,
    bucket: str = RAW_BUCKET,
    country_codes: Sequence[str] | None = None,
    country_names: Mapping[str, Sequence[str]] | None = None,
    session: requests.Session | None = None,
    s3: Any | None = None,
    pause_seconds: float = REQUEST_PAUSE_SECONDS,
) -> dict[str, Any]:
    """Fetch the index, resolve our countries, fetch each country, store everything unchanged."""
    if country_codes is None:
        from ingestion.config import load_cities

        country_codes = sorted({c.country_code for c in load_cities()})
    country_names = country_names or country_names_from_bronze(set(country_codes))
    session = session or build_session()
    s3 = s3 or s3_client()

    index_response = session.get(INDEX_URL)
    index_response.raise_for_status()
    mapping = resolve_slugs(country_names, json.loads(index_response.content))

    prefix = PREFIX_TEMPLATE.format(date=snapshot_date.isoformat())
    stale = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    for obj in stale:  # a rerun must not leave files of an earlier mapping behind
        s3.delete_object(Bucket=bucket, Key=obj["Key"])

    ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
    common = {"ingested_at": ingested_at, "snapshot_date": snapshot_date.isoformat()}
    put_json(
        bucket,
        prefix + INDEX_FILE,
        index_response.content,
        {
            **common,
            "source_url": index_response.url,
            "http_status": str(index_response.status_code),
        },
        s3,
    )
    for i, (code, slug) in enumerate(sorted(mapping.items())):
        if i and pause_seconds:
            time.sleep(pause_seconds)
        response = session.get(COUNTRY_URL.format(slug=slug))
        response.raise_for_status()
        doc = parse_advice(response.content)
        metadata = {
            **common,
            "source_url": response.url,
            "http_status": str(response.status_code),
            "country_code": code,
            "slug": slug,
            "public_updated_at": doc.get("public_updated_at") or "",
        }
        put_json(bucket, f"{prefix}{code}.json", response.content, metadata, s3)

    result = {"bucket": bucket, "prefix": prefix, "countries": len(mapping)}
    log_event(
        log, "fcdo.ingested", **result, mapping=",".join(f"{c}={s}" for c, s in mapping.items())
    )
    return result


# --- raw -> bronze ------------------------------------------------------------------------------


def advice_row(
    doc: dict[str, Any],
    snapshot_date: date,
    country_code: str,
    slug: str,
    ingested_at: datetime,
    source_file: str,
    dag_run_id: str | None,
    loaded_at: datetime,
) -> dict[str, Any]:
    details = doc["details"]
    published = doc.get("public_updated_at")
    return {
        "snapshot_date": snapshot_date,
        "country_code": country_code,
        "slug": slug,
        "title": doc["title"],
        "public_updated_at": datetime.fromisoformat(published) if published else None,
        "change_description": details.get("change_description"),
        "alert_status_json": json.dumps(details.get("alert_status") or [], ensure_ascii=False),
        # Sections exactly as received: slug, title and the HTML body (no cleaning in bronze).
        "parts_json": json.dumps(
            [{"slug": p["slug"], "title": p["title"], "body": p["body"]} for p in details["parts"]],
            ensure_ascii=False,
        ),
        "_ingested_at": ingested_at,
        "_loaded_at": loaded_at,
        "_source_file": source_file,
        "_dag_run_id": dag_run_id,
    }


def load_bronze(
    snapshot_date: date,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    country_codes: Sequence[str] | None = None,
    catalog: Any | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    """Load one snapshot into bronze, replacing any earlier load of the same snapshot_date."""
    if country_codes is None:
        from ingestion.config import load_cities

        country_codes = sorted({c.country_code for c in load_cities()})
    s3 = s3 or s3_client()
    prefix = PREFIX_TEMPLATE.format(date=snapshot_date.isoformat())
    keys = [
        o["Key"]
        for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
        if not o["Key"].endswith(INDEX_FILE)
    ]
    found = {k.rsplit("/", 1)[1].removesuffix(".json"): k for k in keys}
    missing = sorted(set(country_codes) - set(found))
    if missing:
        raise FcdoError(f"no raw advice for {missing} under s3://{bucket}/{prefix}")

    loaded_at = datetime.now(UTC)
    rows = []
    for code in sorted(country_codes):
        obj = s3.get_object(Bucket=bucket, Key=found[code])
        meta = {k.lower(): v for k, v in obj.get("Metadata", {}).items()}
        doc = parse_advice(obj["Body"].read())
        rows.append(
            advice_row(
                doc,
                snapshot_date,
                code,
                meta["slug"],
                datetime.fromisoformat(meta["ingested_at"]),
                f"s3://{bucket}/{found[code]}",
                dag_run_id,
                loaded_at,
            )
        )
    batch = pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA.as_arrow())
    table = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    written = replace_rows(table, batch, "snapshot_date", snapshot_date.isoformat())
    result = {"table": BRONZE_TABLE, "snapshot_date": snapshot_date.isoformat(), "rows": written}
    log_event(log, "fcdo.bronze_loaded", **result)
    return result
