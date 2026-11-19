"""REST Countries v5: country reference data, as a weekly full snapshot.

Source (API key required, free plan: 1,000 requests a month, at most 100 records per request):
    GET https://api.restcountries.com/countries/v5?limit=100&offset=N&response_fields=...
    Authorization: Bearer $REST_COUNTRIES_API_KEY
Versions v1-v4 (e.g. /v3.1/all) are deprecated: they answer HTTP 200 with
{"success": false, "errors": [...]}, so a response is only accepted if it is a v5 country list.

A snapshot is 3 pages (about 254 entities). Each page is stored unchanged:
    raw     s3://raw/rest_countries/all/snapshot_date=YYYY-MM-DD/page=N.json
    bronze  iceberg.bronze.rest_countries, one row per country (ISO 3166 code) and snapshot_date

Rules:
    - v5 also lists entities without an ISO 3166 code (alpha_2 == "", e.g. Abkhazia). They stay in
      raw, are left out of bronze, and are logged. A malformed or duplicate code fails the run, and
      so does a snapshot with fewer than 200 coded countries.
    - Bronze keeps nested values (capitals, languages, currencies, timezones) as JSON strings,
      exactly as received; flattening them into tables is the silver layer's job.
    - `capital` is the capital marked primary, otherwise the first one (all are in capitals_json).
    - `independent` comes from v5's classification.sovereign (v5 has no "independent" field).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import requests
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion.common.http import build_session
from ingestion.common.iceberg import ensure_table, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_json, s3_client

log = get_logger(__name__)

BASE_URL = "https://api.restcountries.com/countries/v5"
API_KEY_ENV = "REST_COUNTRIES_API_KEY"
PAGE_SIZE = 100  # maximum on the free plan
MAX_PAGES = 10  # safety stop (a snapshot is 3 pages)
MIN_COUNTRIES = 200
RESPONSE_FIELDS = ",".join(
    [
        "names.common",
        "names.official",
        "codes.alpha_2",
        "codes.alpha_3",
        "capitals",
        "region",
        "subregion",
        "languages",
        "currencies",
        "population",
        "area.kilometers",
        "timezones",
        "coordinates",
        "flag.url_png",
        "classification.sovereign",
        "classification.un_member",
    ]
)
PREFIX_TEMPLATE = "rest_countries/all/snapshot_date={date}/"
RAW_BUCKET = "raw"
BRONZE_TABLE = "bronze.rest_countries"
_ALPHA_2 = re.compile(r"[A-Z]{2}")

BRONZE_SCHEMA = Schema(
    NestedField(1, "snapshot_date", DateType(), required=True, doc="Date of the weekly snapshot"),
    NestedField(2, "cca2", StringType(), required=True, doc="ISO 3166-1 alpha-2"),
    NestedField(3, "cca3", StringType(), required=False, doc="ISO 3166-1 alpha-3"),
    NestedField(4, "name_common", StringType(), required=True),
    NestedField(5, "name_official", StringType(), required=False),
    NestedField(6, "region", StringType(), required=False),
    NestedField(7, "subregion", StringType(), required=False),
    NestedField(8, "capital", StringType(), required=False, doc="Primary (else first) capital"),
    NestedField(9, "capitals_json", StringType(), required=True, doc="All capitals, JSON"),
    NestedField(10, "languages_json", StringType(), required=True, doc="JSON as received"),
    NestedField(11, "currencies_json", StringType(), required=True, doc="JSON as received"),
    NestedField(12, "population", LongType(), required=False),
    NestedField(13, "area_km2", DoubleType(), required=False),
    NestedField(14, "timezones_json", StringType(), required=True, doc="JSON as received"),
    NestedField(15, "lat", DoubleType(), required=False),
    NestedField(16, "lon", DoubleType(), required=False),
    NestedField(17, "flag_png_url", StringType(), required=False),
    NestedField(18, "independent", BooleanType(), required=False, doc="v5 sovereign"),
    NestedField(19, "un_member", BooleanType(), required=False),
    NestedField(20, "_ingested_at", TimestamptzType(), required=True, doc="Raw object written"),
    NestedField(21, "_loaded_at", TimestamptzType(), required=True, doc="Bronze row written"),
    NestedField(22, "_source_file", StringType(), required=True, doc="Raw page it came from"),
    NestedField(23, "_dag_run_id", StringType(), required=False, doc="Airflow run that loaded it"),
)

# Yearly partitions: one snapshot a week is about 250 rows, so a year is about 13,000 rows.
BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=YearTransform(), name="snapshot_year")
)

BRONZE_ARROW_SCHEMA = pa.schema(
    [
        pa.field("snapshot_date", pa.date32(), nullable=False),
        pa.field("cca2", pa.string(), nullable=False),
        pa.field("cca3", pa.string()),
        pa.field("name_common", pa.string(), nullable=False),
        pa.field("name_official", pa.string()),
        pa.field("region", pa.string()),
        pa.field("subregion", pa.string()),
        pa.field("capital", pa.string()),
        pa.field("capitals_json", pa.string(), nullable=False),
        pa.field("languages_json", pa.string(), nullable=False),
        pa.field("currencies_json", pa.string(), nullable=False),
        pa.field("population", pa.int64()),
        pa.field("area_km2", pa.float64()),
        pa.field("timezones_json", pa.string(), nullable=False),
        pa.field("lat", pa.float64()),
        pa.field("lon", pa.float64()),
        pa.field("flag_png_url", pa.string()),
        pa.field("independent", pa.bool_()),
        pa.field("un_member", pa.bool_()),
        pa.field("_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("_loaded_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("_source_file", pa.string(), nullable=False),
        pa.field("_dag_run_id", pa.string()),
    ]
)


class RestCountriesError(RuntimeError):
    """Raised when the API returns an unusable snapshot or a check fails."""


@dataclass(frozen=True)
class Page:
    number: int
    offset: int
    body: bytes  # exactly as received
    source_url: str
    http_status: int
    records: list[dict[str, Any]]
    total: int
    more: bool


def build_prefix(snapshot_date: date) -> str:
    return PREFIX_TEMPLATE.format(date=snapshot_date.isoformat())


def build_page_key(snapshot_date: date, page: int) -> str:
    return f"{build_prefix(snapshot_date)}page={page}.json"


def api_session(env: dict[str, str] | None = None) -> requests.Session:
    """HTTP session (timeouts, retries) with the API key; the key is never logged."""
    key = (env if env is not None else os.environ).get(API_KEY_ENV)
    if not key:
        raise RestCountriesError(f"missing environment variable {API_KEY_ENV}")
    session = build_session()
    session.headers["Authorization"] = f"Bearer {key}"
    return session


# --- parsing and validation -----------------------------------------------------------------------


def parse_page(body: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (records, meta) of one v5 list page. Anything else (e.g. v3.1's deprecation) fails."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise RestCountriesError("response is not valid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("objects"), list):
        errors = payload.get("errors") if isinstance(payload, dict) else None
        raise RestCountriesError(f"not a v5 country list (errors: {errors!r})")
    meta = data.get("meta") or {}
    if not isinstance(meta.get("total"), int):
        raise RestCountriesError("v5 response has no meta.total")
    return data["objects"], meta


def alpha_2(record: dict[str, Any]) -> str:
    return ((record.get("codes") or {}).get("alpha_2") or "").strip()


def validate_snapshot(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (coded countries, names of entities without an ISO code). Fails loudly otherwise."""
    coded: list[dict[str, Any]] = []
    uncoded: list[str] = []
    bad: list[str] = []
    for r in records:
        code = alpha_2(r)
        name = (r.get("names") or {}).get("common") or "?"
        if not code:
            uncoded.append(name)
        elif not _ALPHA_2.fullmatch(code):
            bad.append(f"{name}={code!r}")
        else:
            coded.append(r)
    if bad:
        raise RestCountriesError(f"malformed alpha_2 codes: {', '.join(bad)}")
    codes = [alpha_2(r) for r in coded]
    duplicates = sorted({c for c in codes if codes.count(c) > 1})
    if duplicates:
        raise RestCountriesError(f"duplicate alpha_2 codes: {', '.join(duplicates)}")
    if len(coded) < MIN_COUNTRIES:
        raise RestCountriesError(
            f"only {len(coded)} countries with an ISO code (expected at least {MIN_COUNTRIES})"
        )
    return coded, uncoded


# --- raw ----------------------------------------------------------------------------------------


def fetch_snapshot(session: requests.Session | None = None) -> list[Page]:
    """Fetch every page of the country list."""
    session = session or api_session()
    pages: list[Page] = []
    offset = 0
    for number in range(1, MAX_PAGES + 1):
        params = {"limit": PAGE_SIZE, "offset": offset, "response_fields": RESPONSE_FIELDS}
        response = session.get(BASE_URL, params=params)
        response.raise_for_status()
        records, meta = parse_page(response.content)
        pages.append(
            Page(
                number=number,
                offset=offset,
                body=response.content,
                source_url=response.url,  # the key is in a header, never in the URL
                http_status=response.status_code,
                records=records,
                total=meta["total"],
                more=bool(meta.get("more")),
            )
        )
        offset += len(records)
        if not meta.get("more") or not records:
            break
    else:
        raise RestCountriesError(f"more than {MAX_PAGES} pages; stopping")

    received = sum(len(p.records) for p in pages)
    if received != pages[0].total:
        raise RestCountriesError(f"received {received} records but meta.total is {pages[0].total}")
    return pages


def ingest_snapshot(
    snapshot_date: date,
    bucket: str = RAW_BUCKET,
    session: requests.Session | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    """Fetch the full list, validate it, and store every page unchanged in the raw bucket."""
    pages = fetch_snapshot(session=session)
    coded, uncoded = validate_snapshot([r for p in pages for r in p.records])

    s3 = s3 or s3_client()
    prefix = build_prefix(snapshot_date)
    stale = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    for obj in stale:  # a rerun must not leave pages of an earlier, longer snapshot behind
        s3.delete_object(Bucket=bucket, Key=obj["Key"])

    ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
    for page in pages:
        metadata = {
            "ingested_at": ingested_at,
            "source_url": page.source_url,
            "http_status": str(page.http_status),
            "snapshot_date": snapshot_date.isoformat(),
            "record_count": str(len(page.records)),
            "page": str(page.number),
            "page_count": str(len(pages)),
            "offset": str(page.offset),
            "total": str(page.total),
        }
        put_json(bucket, build_page_key(snapshot_date, page.number), page.body, metadata, s3)

    result = {
        "bucket": bucket,
        "prefix": prefix,
        "pages": len(pages),
        "records": pages[0].total,
        "countries": len(coded),
        "without_iso_code": len(uncoded),
    }
    log_event(log, "rest_countries.ingested", **result, uncoded=",".join(uncoded))
    return result


# --- raw -> bronze ------------------------------------------------------------------------------


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, sort_keys=True)


def _capital(capitals: list[dict[str, Any]]) -> str | None:
    if not capitals:
        return None
    primary = next((c for c in capitals if (c.get("attributes") or {}).get("primary")), capitals[0])
    return primary.get("name")


def records_to_arrow(
    records: list[tuple[dict[str, Any], str]],
    snapshot_date: date,
    ingested_at: datetime,
    dag_run_id: str | None,
    loaded_at: datetime | None = None,
) -> pa.Table:
    """Bronze rows from (record, source_file) pairs of coded countries."""
    loaded_at = loaded_at or datetime.now(UTC)
    rows = []
    for r, source_file in records:
        names = r.get("names") or {}
        codes = r.get("codes") or {}
        coords = r.get("coordinates") or {}
        classification = r.get("classification") or {}
        area = (r.get("area") or {}).get("kilometers")
        capitals = r.get("capitals") or []
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "cca2": codes.get("alpha_2"),
                "cca3": codes.get("alpha_3") or None,
                "name_common": names.get("common"),
                "name_official": names.get("official"),
                "region": r.get("region") or None,
                "subregion": r.get("subregion") or None,
                "capital": _capital(capitals),
                "capitals_json": _json(capitals),
                "languages_json": _json(r.get("languages")),
                "currencies_json": _json(r.get("currencies")),
                "population": r.get("population"),
                "area_km2": float(area) if area is not None else None,
                "timezones_json": _json(r.get("timezones")),
                "lat": coords.get("lat"),
                "lon": coords.get("lng"),
                "flag_png_url": (r.get("flag") or {}).get("url_png"),
                "independent": classification.get("sovereign"),
                "un_member": classification.get("un_member"),
                "_ingested_at": ingested_at,
                "_loaded_at": loaded_at,
                "_source_file": source_file,
                "_dag_run_id": dag_run_id,
            }
        )
    return pa.Table.from_pylist(rows, schema=BRONZE_ARROW_SCHEMA)


def read_snapshot(
    snapshot_date: date, bucket: str = RAW_BUCKET, s3: Any | None = None
) -> tuple[list[tuple[dict[str, Any], str]], datetime]:
    """Read all raw pages of a snapshot; returns ((record, source_file) pairs, ingested_at)."""
    s3 = s3 or s3_client()
    prefix = build_prefix(snapshot_date)
    keys = sorted(
        (o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])),
        key=lambda k: int(k.rsplit("page=", 1)[1].split(".")[0]),
    )
    if not keys:
        raise RestCountriesError(f"no raw pages under s3://{bucket}/{prefix}")
    pairs: list[tuple[dict[str, Any], str]] = []
    total = page_count = None
    ingested_at = None
    for key in keys:
        obj = s3.get_object(Bucket=bucket, Key=key)
        meta = {k.lower(): v for k, v in obj.get("Metadata", {}).items()}
        records, _ = parse_page(obj["Body"].read())
        total, page_count = int(meta["total"]), int(meta["page_count"])
        ingested_at = datetime.fromisoformat(meta["ingested_at"])
        pairs.extend((r, f"s3://{bucket}/{key}") for r in records)
    if len(keys) != page_count or len(pairs) != total:
        raise RestCountriesError(
            f"incomplete snapshot: {len(keys)}/{page_count} pages, {len(pairs)}/{total} records"
        )
    assert ingested_at is not None
    return pairs, ingested_at


def check_city_countries(country_codes: set[str], cities_country_codes: dict[str, str]) -> None:
    """Fail if a city's country (config/cities.yaml) is missing from the snapshot."""
    missing = {city: cc for city, cc in cities_country_codes.items() if cc not in country_codes}
    if missing:
        detail = ", ".join(f"{city} ({cc})" for city, cc in sorted(missing.items()))
        log_event(log, "rest_countries.city_countries_missing", missing=detail)
        raise RestCountriesError(
            f"countries of these cities are missing from the snapshot: {detail}"
        )
    log_event(log, "rest_countries.city_countries_ok", cities=len(cities_country_codes))


def load_bronze(
    snapshot_date: date,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    catalog: Any | None = None,
    s3: Any | None = None,
    cities_country_codes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load one snapshot into bronze, replacing any earlier load of the same snapshot_date."""
    pairs, ingested_at = read_snapshot(snapshot_date, bucket=bucket, s3=s3)
    _, uncoded = validate_snapshot([r for r, _ in pairs])
    coded_pairs = [(r, src) for r, src in pairs if alpha_2(r)]  # entities with an ISO code
    batch = records_to_arrow(coded_pairs, snapshot_date, ingested_at, dag_run_id)

    if cities_country_codes is None:
        from ingestion.config import load_cities

        cities_country_codes = {c.id: c.country_code for c in load_cities()}
    check_city_countries(set(batch["cca2"].to_pylist()), cities_country_codes)

    table = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    rows = replace_rows(table, batch, "snapshot_date", snapshot_date.isoformat())
    result = {
        "table": BRONZE_TABLE,
        "snapshot_date": snapshot_date.isoformat(),
        "rows": rows,
        "skipped_without_iso_code": len(uncoded),
    }
    log_event(log, "rest_countries.bronze_loaded", **result, skipped=",".join(uncoded))
    return result
