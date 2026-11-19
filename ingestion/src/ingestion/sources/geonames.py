"""GeoNames: cities with a population above 15,000 (or capitals), as a monthly snapshot.

Source (free data dump, no account):
    https://download.geonames.org/export/dump/cities15000.zip  (one file: cities15000.txt)
Format: tab-separated UTF-8, no header, no quoting; the 19 columns of the 'geoname' table in
https://download.geonames.org/export/dump/readme.txt.

License: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution:
    City data (c) GeoNames (https://www.geonames.org), licensed under CC BY 4.0.

Layers:
    raw     s3://raw/geonames/cities15000/snapshot_date=YYYY-MM-DD/cities15000.zip (unchanged)
    bronze  iceberg.bronze.geonames_cities, one row per place and snapshot_date
"""

from __future__ import annotations

import hashlib
import io
import math
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import requests
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion.common.http import build_session
from ingestion.common.iceberg import ensure_table, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_object, s3_client

log = get_logger(__name__)

SOURCE_URL = "https://download.geonames.org/export/dump/cities15000.zip"
MEMBER = "cities15000.txt"
KEY_TEMPLATE = "geonames/cities15000/snapshot_date={date}/cities15000.zip"
RAW_BUCKET = "raw"
BRONZE_TABLE = "bronze.geonames_cities"
MIN_ROWS = 20_000
MAX_MATCH_KM = 50.0  # city centres differ between sources (Dubai: about 21 km)
PREFERRED_FEATURES = ("PPLC", "PPLA", "PPL")  # capital, admin seat, populated place

# The 19 columns of the 'geoname' table (readme.txt), in file order, with their types.
# Code columns stay strings: admin codes such as "01" must keep their leading zero.
SOURCE_COLUMNS: list[tuple[str, pa.DataType]] = [
    ("geonameid", pa.int64()),
    ("name", pa.string()),
    ("asciiname", pa.string()),
    ("alternatenames", pa.string()),  # comma separated, kept as received
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
    ("feature_class", pa.string()),
    ("feature_code", pa.string()),
    ("country_code", pa.string()),
    ("cc2", pa.string()),
    ("admin1_code", pa.string()),
    ("admin2_code", pa.string()),
    ("admin3_code", pa.string()),
    ("admin4_code", pa.string()),
    ("population", pa.int64()),
    ("elevation", pa.int32()),
    ("dem", pa.int32()),
    ("timezone", pa.string()),
    ("modification_date", pa.date32()),
]

_ICEBERG_TYPES = {
    pa.int64(): LongType(),
    pa.int32(): IntegerType(),
    pa.float64(): DoubleType(),
    pa.string(): StringType(),
    pa.date32(): DateType(),
}
_REQUIRED = {"geonameid", "name", "latitude", "longitude"}

BRONZE_SCHEMA = Schema(
    NestedField(1, "snapshot_date", DateType(), required=True, doc="Date of the monthly snapshot"),
    *[
        NestedField(i, name, _ICEBERG_TYPES[typ], required=name in _REQUIRED)
        for i, (name, typ) in enumerate(SOURCE_COLUMNS, start=2)
    ],
    NestedField(21, "_ingested_at", TimestamptzType(), required=True, doc="Raw object written"),
    NestedField(22, "_loaded_at", TimestamptzType(), required=True, doc="Bronze row written"),
    NestedField(23, "_source_file", StringType(), required=True, doc="Full s3:// key of raw zip"),
    NestedField(24, "_dag_run_id", StringType(), required=False, doc="Airflow run that loaded it"),
)

# Yearly partitions: about 34,000 rows a month, 12 snapshots a year. Each monthly load writes
# its own files, so replacing one snapshot only drops that snapshot's files.
BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=YearTransform(), name="snapshot_year")
)


class GeoNamesError(RuntimeError):
    """Raised when the dump is unusable or a check fails."""


@dataclass(frozen=True)
class Download:
    body: bytes  # the zip exactly as downloaded
    source_url: str
    http_status: int
    last_modified: str | None
    sha256: str


def build_key(snapshot_date: date) -> str:
    return KEY_TEMPLATE.format(date=snapshot_date.isoformat())


# --- parsing and validation -----------------------------------------------------------------------


def parse_cities(zip_bytes: bytes) -> pa.Table:
    """Parse cities15000.txt from the zip: explicit names and types, empty values as null."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise GeoNamesError("download is not a zip file") from exc
    if MEMBER not in archive.namelist():
        raise GeoNamesError(f"{MEMBER} not in the zip (members: {archive.namelist()})")
    with archive.open(MEMBER) as f:
        try:
            return pacsv.read_csv(
                f,
                read_options=pacsv.ReadOptions(column_names=[n for n, _ in SOURCE_COLUMNS]),
                # GeoNames does not quote fields, and names can contain a literal '"'.
                parse_options=pacsv.ParseOptions(delimiter="\t", quote_char=False),
                convert_options=pacsv.ConvertOptions(
                    column_types=dict(SOURCE_COLUMNS),
                    strings_can_be_null=True,  # "" -> null for text columns too
                    null_values=[""],
                ),
            )
        except pa.ArrowInvalid as exc:
            raise GeoNamesError(f"cannot parse {MEMBER}: {exc}") from exc


def validate(table: pa.Table, min_rows: int | None = None) -> None:
    """More than MIN_ROWS rows, unique geonameid, coordinates in range. Fails loudly."""
    min_rows = MIN_ROWS if min_rows is None else min_rows
    if table.num_rows <= min_rows:
        raise GeoNamesError(f"only {table.num_rows} rows (expected more than {min_rows})")
    ids = table["geonameid"]
    if ids.null_count:
        raise GeoNamesError(f"{ids.null_count} rows without geonameid")
    if pc.count_distinct(ids).as_py() != table.num_rows:
        raise GeoNamesError("geonameid is not unique")
    for column, limit in (("latitude", 90), ("longitude", 180)):
        values = table[column]
        out = pc.sum(pc.or_kleene(pc.less(values, -limit), pc.greater(values, limit))).as_py()
        if values.null_count or out:
            raise GeoNamesError(f"{column}: {values.null_count} null, {out or 0} out of range")


# --- raw ----------------------------------------------------------------------------------------


def download(session: requests.Session | None = None) -> Download:
    """Download the zip (streamed to a temporary file to keep memory low)."""
    session = session or build_session()
    digest = hashlib.sha256()
    with (
        session.get(SOURCE_URL, stream=True) as response,
        tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp,
    ):
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            digest.update(chunk)
            tmp.write(chunk)
        tmp.seek(0)
        body = tmp.read()
        return Download(
            body=body,
            source_url=response.url,
            http_status=response.status_code,
            last_modified=response.headers.get("Last-Modified"),
            sha256=digest.hexdigest(),
        )


def ingest_snapshot(
    snapshot_date: date,
    bucket: str = RAW_BUCKET,
    session: requests.Session | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    """Download, validate and store the zip unchanged in the raw bucket."""
    result = download(session=session)
    table = parse_cities(result.body)
    validate(table)
    key = build_key(snapshot_date)
    metadata = {
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": result.source_url,
        "http_status": str(result.http_status),
        "snapshot_date": snapshot_date.isoformat(),
        "record_count": str(table.num_rows),
        "file_size": str(len(result.body)),
        "sha256": result.sha256,
        "last_modified": result.last_modified or "",
    }
    put_object(bucket, key, result.body, "application/zip", metadata=metadata, client=s3)
    log_event(log, "geonames.ingested", bucket=bucket, key=key, **metadata)
    return {"bucket": bucket, "key": key, **metadata}


# --- city matching ------------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    h = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Match:
    geonameid: int
    name: str
    feature_code: str | None
    population: int | None
    distance_km: float
    candidates: int


def match_city(
    name: str, country_code: str, latitude: float, longitude: float, table: pa.Table
) -> Match:
    """Find a city's geonameid: same country, name or asciiname equal (ignoring case),
    preferring PPLC/PPLA/PPL, then the closest to our coordinates (at most MAX_MATCH_KM)."""
    wanted = name.casefold()
    cols = ["geonameid", "name", "asciiname", "latitude", "longitude", "feature_code", "population"]
    in_country = table.filter(pc.equal(table["country_code"], country_code)).select(cols)
    candidates = [
        r
        for r in in_country.to_pylist()
        if wanted in ((r["name"] or "").casefold(), (r["asciiname"] or "").casefold())
    ]
    if not candidates:
        raise GeoNamesError(f"{name} ({country_code}): no GeoNames place with that name")
    preferred = [r for r in candidates if r["feature_code"] in PREFERRED_FEATURES]
    pool = preferred or candidates
    best = min(pool, key=lambda r: haversine_km(latitude, longitude, r["latitude"], r["longitude"]))
    distance = haversine_km(latitude, longitude, best["latitude"], best["longitude"])
    if distance > MAX_MATCH_KM:
        raise GeoNamesError(f"{name} ({country_code}): closest match is {distance:.1f} km away")
    return Match(
        best["geonameid"],
        best["name"],
        best["feature_code"],
        best["population"],
        round(distance, 2),
        len(candidates),
    )


def check_city_ids(snapshot_ids: set[int], city_ids: dict[str, int]) -> None:
    """Fail if a configured geonames_id (config/cities.yaml) is missing from the snapshot."""
    missing = {city: gid for city, gid in city_ids.items() if gid not in snapshot_ids}
    if missing:
        detail = ", ".join(f"{city} ({gid})" for city, gid in sorted(missing.items()))
        log_event(log, "geonames.city_ids_missing", missing=detail)
        raise GeoNamesError(f"geonames_id of these cities is missing from the snapshot: {detail}")
    log_event(log, "geonames.city_ids_ok", cities=len(city_ids))


# --- raw -> bronze ------------------------------------------------------------------------------


def to_bronze(
    table: pa.Table,
    snapshot_date: date,
    ingested_at: datetime,
    source_file: str,
    dag_run_id: str | None,
    loaded_at: datetime | None = None,
) -> pa.Table:
    """Add snapshot_date and the ingestion columns to the parsed rows."""
    n = table.num_rows
    loaded_at = loaded_at or datetime.now(UTC)
    ts = pa.timestamp("us", tz="UTC")
    columns = {
        "snapshot_date": pa.array([snapshot_date] * n, pa.date32()),
        **{name: table[name] for name, _ in SOURCE_COLUMNS},
        "_ingested_at": pa.array([ingested_at] * n, ts),
        "_loaded_at": pa.array([loaded_at] * n, ts),
        "_source_file": pa.array([source_file] * n, pa.string()),
        "_dag_run_id": pa.array([dag_run_id] * n, pa.string()),
    }
    return pa.table(columns, schema=BRONZE_SCHEMA.as_arrow())


def load_bronze(
    snapshot_date: date,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    catalog: Any | None = None,
    s3: Any | None = None,
    city_ids: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Load one snapshot into bronze, replacing any earlier load of the same snapshot_date."""
    s3 = s3 or s3_client()
    key = build_key(snapshot_date)
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    meta = {k.lower(): v for k, v in obj.get("Metadata", {}).items()}
    if meta.get("sha256") and hashlib.sha256(body).hexdigest() != meta["sha256"]:
        raise GeoNamesError(f"s3://{bucket}/{key}: sha256 does not match its metadata")

    table = parse_cities(body)
    validate(table)

    if city_ids is None:
        from ingestion.config import load_cities

        city_ids = {c.id: c.geonames_id for c in load_cities()}
    check_city_ids(set(table["geonameid"].to_pylist()), city_ids)

    batch = to_bronze(
        table,
        snapshot_date,
        datetime.fromisoformat(meta["ingested_at"]),
        f"s3://{bucket}/{key}",
        dag_run_id,
    )
    target = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    rows = replace_rows(target, batch, "snapshot_date", snapshot_date.isoformat())
    result = {"table": BRONZE_TABLE, "snapshot_date": snapshot_date.isoformat(), "rows": rows}
    log_event(log, "geonames.bronze_loaded", **result)
    return result
