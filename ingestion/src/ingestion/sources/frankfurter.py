"""Frankfurter API: daily ECB reference exchange rates (base EUR).

Endpoint (Frankfurter v2; v1 is deprecated):
    GET https://api.frankfurter.dev/v2/providers/ecb/rates?date=YYYY-MM-DD&base=EUR

The response is a JSON list of rows: {"date", "base", "quote", "rate"}.
On weekends, holidays, or before the ECB publishes (~16:00 CET), the API returns the
last available business day, so the returned rate date can be earlier than the requested one.
A date with no data at all (e.g. in the future) returns an empty list, which we treat as an error.

Layers:
    raw     s3://raw/frankfurter/rates/date=YYYY-MM-DD/rates.json, the response body unchanged
    bronze  iceberg.bronze.frankfurter_rates, one row per quote currency and requested date
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pyarrow as pa
import requests
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import MonthTransform
from pyiceberg.types import (
    DateType,
    DecimalType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion.common.http import build_session
from ingestion.common.iceberg import ensure_table, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_json, s3_client

log = get_logger(__name__)

BASE_URL = "https://api.frankfurter.dev/v2"
RATES_PATH = "/providers/ecb/rates"
BASE_CURRENCY = "EUR"
KEY_TEMPLATE = "frankfurter/rates/date={date}/rates.json"
RAW_BUCKET = "raw"
BRONZE_TABLE = "bronze.frankfurter_rates"

# decimal(18, 8): 10 integer digits (IDR is about 20,000 per EUR) and 8 decimals (the ECB publishes
# at most 5, e.g. 0.00061). Rates are parsed as Decimal, never as binary floats.
RATE_TYPE = DecimalType(18, 8)

# Bronze keeps the data as received. Weekend and holiday runs repeat the last business day's rates
# under their own requested_date (rate_date shows the real day). Deduplication by rate_date is the
# silver layer's job, not bronze's.
BRONZE_SCHEMA = Schema(
    NestedField(1, "requested_date", DateType(), required=True, doc="Date the API was asked for"),
    NestedField(2, "rate_date", DateType(), required=True, doc="Business day the rates are for"),
    NestedField(3, "base_currency", StringType(), required=True),
    NestedField(4, "quote_currency", StringType(), required=True),
    NestedField(5, "rate", RATE_TYPE, required=True, doc="Units of quote currency per base unit"),
    NestedField(6, "_ingested_at", TimestamptzType(), required=True, doc="Raw object written"),
    NestedField(7, "_loaded_at", TimestamptzType(), required=True, doc="Bronze row written"),
    NestedField(8, "_source_file", StringType(), required=True, doc="Full s3:// key of raw file"),
    NestedField(9, "_dag_run_id", StringType(), required=False, doc="Airflow run that loaded it"),
)

# Monthly, not daily, partitions: about 30 rows arrive per day, so daily partitions would create
# many tiny files and a lot of metadata. A month is about 900 rows in a few files, and queries on a
# date range still skip whole months.
BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=MonthTransform(), name="requested_month")
)

BRONZE_ARROW_SCHEMA = pa.schema(
    [
        pa.field("requested_date", pa.date32(), nullable=False),
        pa.field("rate_date", pa.date32(), nullable=False),
        pa.field("base_currency", pa.string(), nullable=False),
        pa.field("quote_currency", pa.string(), nullable=False),
        pa.field("rate", pa.decimal128(RATE_TYPE.precision, RATE_TYPE.scale), nullable=False),
        pa.field("_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("_loaded_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("_source_file", pa.string(), nullable=False),
        pa.field("_dag_run_id", pa.string(), nullable=True),
    ]
)


class FrankfurterError(RuntimeError):
    """Raised when the API returns an unusable response."""


@dataclass(frozen=True)
class RatesResponse:
    body: bytes  # exactly as received
    source_url: str
    http_status: int
    requested_date: date
    rate_date: date
    rows: int


def build_key(requested_date: date) -> str:
    """Object key for a requested (logical) date. Same date -> same key (idempotent)."""
    return KEY_TEMPLATE.format(date=requested_date.isoformat())


def fetch_rates(
    requested_date: date,
    session: requests.Session | None = None,
    base_url: str = BASE_URL,
) -> RatesResponse:
    """Fetch ECB rates for a date. Returns the raw body plus what we learned from it."""
    session = session or build_session()
    params = {"date": requested_date.isoformat(), "base": BASE_CURRENCY}

    response = session.get(base_url + RATES_PATH, params=params)
    response.raise_for_status()

    try:
        payload: Any = json.loads(response.content)
    except ValueError as exc:
        raise FrankfurterError(f"response is not valid JSON: {response.url}") from exc

    if not isinstance(payload, list):
        raise FrankfurterError(f"expected a JSON list, got {type(payload).__name__}")
    if not payload:
        raise FrankfurterError(f"no rates available for {requested_date.isoformat()}")

    rate_dates = {row["date"] for row in payload}
    if len(rate_dates) != 1:
        raise FrankfurterError(f"expected one rate date, got {sorted(rate_dates)}")
    rate_date = date.fromisoformat(rate_dates.pop())

    result = RatesResponse(
        body=response.content,
        source_url=response.url,
        http_status=response.status_code,
        requested_date=requested_date,
        rate_date=rate_date,
        rows=len(payload),
    )
    log_event(
        log,
        "frankfurter.fetched",
        requested_date=requested_date.isoformat(),
        rate_date=rate_date.isoformat(),
        rows=result.rows,
        status=result.http_status,
        bytes=len(result.body),
    )
    if rate_date != requested_date:
        log_event(
            log,
            "frankfurter.rate_date_differs",
            requested_date=requested_date.isoformat(),
            rate_date=rate_date.isoformat(),
            reason="weekend, holiday, or not yet published",
        )
    return result


def ingest_rates(
    requested_date: date,
    bucket: str,
    session: requests.Session | None = None,
    s3: Any | None = None,
) -> dict[str, str]:
    """Fetch rates for a date and store the raw response in the bronze bucket."""
    result = fetch_rates(requested_date, session=session)
    key = build_key(requested_date)
    metadata = {
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": result.source_url,
        "http_status": str(result.http_status),
        "requested_date": result.requested_date.isoformat(),
        "rate_date": result.rate_date.isoformat(),
    }
    put_json(bucket, key, result.body, metadata=metadata, client=s3)
    log_event(log, "frankfurter.ingested", bucket=bucket, key=key, **metadata)
    return {"bucket": bucket, "key": key, **metadata}


# --- raw -> bronze ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawObject:
    body: bytes
    metadata: dict[str, str]
    source_file: str


def read_raw(requested_date: date, bucket: str = RAW_BUCKET, s3: Any | None = None) -> RawObject:
    """Read the raw object for one requested date: body exactly as stored, plus S3 metadata."""
    s3 = s3 or s3_client()
    key = build_key(requested_date)
    obj = s3.get_object(Bucket=bucket, Key=key)
    return RawObject(
        body=obj["Body"].read(),
        metadata={k.lower(): v for k, v in obj.get("Metadata", {}).items()},
        source_file=f"s3://{bucket}/{key}",
    )


def raw_to_arrow(
    raw: RawObject,
    dag_run_id: str | None,
    loaded_at: datetime | None = None,
) -> pa.Table:
    """Turn one raw response into bronze rows: one row per quote currency."""
    try:
        rows: Any = json.loads(raw.body, parse_float=Decimal)
    except ValueError as exc:
        raise FrankfurterError(f"{raw.source_file}: body is not valid JSON") from exc
    if not isinstance(rows, list) or not rows:
        raise FrankfurterError(f"{raw.source_file}: expected a non-empty JSON list")

    try:
        requested_date = date.fromisoformat(raw.metadata["requested_date"])
        ingested_at = datetime.fromisoformat(raw.metadata["ingested_at"])
    except KeyError as exc:
        raise FrankfurterError(f"{raw.source_file}: missing metadata {exc.args[0]!r}") from exc

    rate_dates = {r["date"] for r in rows}
    if len(rate_dates) != 1:
        found = sorted(rate_dates)
        raise FrankfurterError(f"{raw.source_file}: expected one rate date, got {found}")
    rate_date = date.fromisoformat(rate_dates.pop())
    meta_rate_date = raw.metadata.get("rate_date")
    if meta_rate_date and date.fromisoformat(meta_rate_date) != rate_date:
        raise FrankfurterError(
            f"{raw.source_file}: body rate date {rate_date} != metadata rate_date {meta_rate_date}"
        )

    loaded_at = loaded_at or datetime.now(UTC)
    quantum = Decimal(1).scaleb(-RATE_TYPE.scale)
    n = len(rows)
    columns = {
        "requested_date": [requested_date] * n,
        "rate_date": [rate_date] * n,
        "base_currency": [str(r["base"]) for r in rows],
        "quote_currency": [str(r["quote"]) for r in rows],
        "rate": [Decimal(r["rate"]).quantize(quantum) for r in rows],
        "_ingested_at": [ingested_at] * n,
        "_loaded_at": [loaded_at] * n,
        "_source_file": [raw.source_file] * n,
        "_dag_run_id": [dag_run_id] * n,
    }
    return pa.Table.from_pydict(columns, schema=BRONZE_ARROW_SCHEMA)


def load_bronze(
    requested_date: date,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    catalog: Any | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    """Load one requested date from raw into bronze, replacing any earlier load of that date."""
    raw = read_raw(requested_date, bucket=bucket, s3=s3)
    batch = raw_to_arrow(raw, dag_run_id=dag_run_id)
    table = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    rows = replace_rows(table, batch, "requested_date", requested_date.isoformat())
    result = {
        "table": BRONZE_TABLE,
        "requested_date": requested_date.isoformat(),
        "rate_date": batch["rate_date"][0].as_py().isoformat(),
        "rows": rows,
        "source_file": raw.source_file,
    }
    log_event(log, "frankfurter.bronze_loaded", **result)
    return result
