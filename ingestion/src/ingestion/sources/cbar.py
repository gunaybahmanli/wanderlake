"""CBAR: official daily exchange rates of the Central Bank of Azerbaijan (AZN per foreign unit).

Source (one XML file per date):
    GET https://www.cbar.az/currencies/DD.MM.YYYY.xml

The root element <ValCurs Date="DD.MM.YYYY"> holds two groups:
    <ValType Type="Bank metalları">     bank metals (XAU, XAG, XPT, XPD; nominal "1 t.u.")
    <ValType Type="Xarici valyutalar">  foreign currencies (nominal 1 or 100)
Each <Valute Code=".."> has <Nominal>, <Name> (Azerbaijani) and <Value>, the AZN price of
"nominal" units (e.g. JPY: 100 yen = 1.0653 AZN).

Behaviour observed on 2026-11-19 (not documented by CBAR):
    - a date's file exists during that day (by 13:31 UTC at the latest); schedule after that
    - weekends and holidays return the last published file (ValCurs Date shows its real date)
    - future dates also return HTTP 200 with the latest file, so future requests are refused here

Only foreign currencies are kept: bank metals, and SDR (the IMF's unit of account, not a
currency), are skipped and logged.

Layers:
    raw     s3://raw/cbar/rates/date=YYYY-MM-DD/rates.xml, the response body unchanged
    bronze  iceberg.bronze.cbar_rates, one row per currency and requested date
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element
from zoneinfo import ZoneInfo

import pyarrow as pa
import requests
from defusedxml.ElementTree import ParseError, fromstring
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import MonthTransform
from pyiceberg.types import (
    DateType,
    DecimalType,
    IntegerType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion.common.http import build_session
from ingestion.common.iceberg import ensure_table, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_object, s3_client

log = get_logger(__name__)

URL_TEMPLATE = "https://www.cbar.az/currencies/{day:%d.%m.%Y}.xml"
KEY_TEMPLATE = "cbar/rates/date={date}/rates.xml"
RAW_BUCKET = "raw"
BRONZE_TABLE = "bronze.cbar_rates"
CBAR_TZ = ZoneInfo("Asia/Baku")

CURRENCY_GROUP = "Xarici valyutalar"  # "foreign currencies"; everything else is skipped
NON_CURRENCY_CODES = frozenset({"SDR"})  # IMF Special Drawing Rights: a unit, not a currency

# decimal(18, 6): CBAR publishes 4 decimals (e.g. 0.1276); 6 leaves headroom.
RATE_TYPE = DecimalType(18, 6)

# Bronze keeps the data as received: weekend and holiday runs repeat the last business day's file
# under their own requested_date (rate_date shows the real day). Deduplication is silver's job.
BRONZE_SCHEMA = Schema(
    NestedField(1, "requested_date", DateType(), required=True, doc="Date the file was asked for"),
    NestedField(2, "rate_date", DateType(), required=True, doc="Date stated in the file"),
    NestedField(3, "currency_code", StringType(), required=True),
    NestedField(4, "currency_name", StringType(), required=True, doc="As published (Azerbaijani)"),
    NestedField(5, "nominal", IntegerType(), required=True, doc="Units the rate is quoted for"),
    NestedField(6, "rate_azn", RATE_TYPE, required=True, doc="AZN for `nominal` units"),
    NestedField(7, "_ingested_at", TimestamptzType(), required=True, doc="Raw object written"),
    NestedField(8, "_loaded_at", TimestamptzType(), required=True, doc="Bronze row written"),
    NestedField(9, "_source_file", StringType(), required=True, doc="Full s3:// key of raw file"),
    NestedField(10, "_dag_run_id", StringType(), required=False, doc="Airflow run that loaded it"),
)

# Monthly, not daily, partitions: about 37 rows a day would make many tiny daily files.
BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=MonthTransform(), name="requested_month")
)

BRONZE_ARROW_SCHEMA = pa.schema(
    [
        pa.field("requested_date", pa.date32(), nullable=False),
        pa.field("rate_date", pa.date32(), nullable=False),
        pa.field("currency_code", pa.string(), nullable=False),
        pa.field("currency_name", pa.string(), nullable=False),
        pa.field("nominal", pa.int32(), nullable=False),
        pa.field("rate_azn", pa.decimal128(RATE_TYPE.precision, RATE_TYPE.scale), nullable=False),
        pa.field("_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("_loaded_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("_source_file", pa.string(), nullable=False),
        pa.field("_dag_run_id", pa.string(), nullable=True),
    ]
)

_NOMINAL = re.compile(r"^\d+$")


class CbarError(RuntimeError):
    """Raised when CBAR returns an unusable file or a date cannot be loaded."""


@dataclass(frozen=True)
class Rate:
    code: str
    name: str
    nominal: int
    rate_azn: Decimal


@dataclass(frozen=True)
class ParsedFile:
    rate_date: date
    rates: list[Rate]
    skipped: list[str]  # codes of non-currency entries (metals, SDR)


@dataclass(frozen=True)
class RatesResponse:
    body: bytes  # exactly as received
    source_url: str
    http_status: int
    requested_date: date
    rate_date: date
    rows: int


@dataclass(frozen=True)
class RawObject:
    body: bytes
    metadata: dict[str, str]
    source_file: str


def build_url(requested_date: date) -> str:
    return URL_TEMPLATE.format(day=requested_date)


def build_key(requested_date: date) -> str:
    """Object key for a requested (logical) date. Same date -> same key (idempotent)."""
    return KEY_TEMPLATE.format(date=requested_date.isoformat())


def today_in_baku(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(CBAR_TZ).date()


# --- parsing ------------------------------------------------------------------------------------


def _text(valute: Element, tag: str, code: str) -> str:
    value = valute.findtext(tag)
    if value is None or not value.strip():
        raise CbarError(f"{code}: missing <{tag}>")
    return value.strip()


def parse_rates(body: bytes) -> ParsedFile:
    """Parse a CBAR XML file with a hardened parser. Keeps only foreign currencies."""
    try:
        root = fromstring(body)
    except ParseError as exc:
        raise CbarError(f"invalid XML: {exc}") from exc
    if root.tag != "ValCurs" or not root.get("Date"):
        raise CbarError(f"unexpected root element <{root.tag}>")
    try:
        rate_date = datetime.strptime(root.get("Date", ""), "%d.%m.%Y").date()
    except ValueError as exc:
        raise CbarError(f"unexpected ValCurs Date {root.get('Date')!r}") from exc

    rates: list[Rate] = []
    skipped: list[str] = []
    for group in root.findall("ValType"):
        is_currency_group = group.get("Type") == CURRENCY_GROUP
        for valute in group.findall("Valute"):
            code = (valute.get("Code") or "").strip()
            if not is_currency_group or code in NON_CURRENCY_CODES:
                skipped.append(code)
                continue
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise CbarError(f"unexpected currency code {code!r}")
            nominal = _text(valute, "Nominal", code)
            if not _NOMINAL.match(nominal):
                raise CbarError(f"{code}: unexpected nominal {nominal!r}")
            value = _text(valute, "Value", code)
            try:
                rate = Decimal(value)
            except InvalidOperation as exc:
                raise CbarError(f"{code}: unexpected value {value!r}") from exc
            rates.append(Rate(code, _text(valute, "Name", code), int(nominal), rate))

    if not rates:
        raise CbarError("no foreign currencies in the file")
    codes = [r.code for r in rates]
    if len(codes) != len(set(codes)):
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        raise CbarError(f"duplicate currency codes: {duplicates}")
    return ParsedFile(rate_date=rate_date, rates=rates, skipped=skipped)


# --- raw ----------------------------------------------------------------------------------------


def fetch_rates(
    requested_date: date,
    session: requests.Session | None = None,
    today: date | None = None,
) -> RatesResponse:
    """Fetch the CBAR file for a date. Future dates are refused (CBAR would return today's file)."""
    today = today or today_in_baku()
    if requested_date > today:
        raise CbarError(f"{requested_date} is in the future (today in Baku is {today})")

    session = session or build_session()
    url = build_url(requested_date)
    response = session.get(url, headers={"Accept": "application/xml"})
    response.raise_for_status()

    parsed = parse_rates(response.content)
    if parsed.rate_date > requested_date:
        raise CbarError(f"file date {parsed.rate_date} is after the requested {requested_date}")

    result = RatesResponse(
        body=response.content,
        source_url=response.url,
        http_status=response.status_code,
        requested_date=requested_date,
        rate_date=parsed.rate_date,
        rows=len(parsed.rates),
    )
    log_event(
        log,
        "cbar.fetched",
        requested_date=requested_date.isoformat(),
        rate_date=parsed.rate_date.isoformat(),
        currencies=result.rows,
        skipped=",".join(parsed.skipped),
        status=result.http_status,
        bytes=len(result.body),
    )
    return result


def ingest_rates(
    requested_date: date,
    bucket: str = RAW_BUCKET,
    session: requests.Session | None = None,
    s3: Any | None = None,
    today: date | None = None,
) -> dict[str, str]:
    """Fetch the file for a date and store it unchanged in the raw bucket."""
    result = fetch_rates(requested_date, session=session, today=today)
    key = build_key(requested_date)
    metadata = {
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": result.source_url,
        "http_status": str(result.http_status),
        "requested_date": result.requested_date.isoformat(),
        "rate_date": result.rate_date.isoformat(),
    }
    put_object(bucket, key, result.body, "application/xml", metadata=metadata, client=s3)
    log_event(log, "cbar.ingested", bucket=bucket, key=key, **metadata)
    return {"bucket": bucket, "key": key, **metadata}


# --- raw -> bronze ------------------------------------------------------------------------------


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
    """Turn one raw file into bronze rows: one row per foreign currency."""
    parsed = parse_rates(raw.body)
    try:
        requested_date = date.fromisoformat(raw.metadata["requested_date"])
        ingested_at = datetime.fromisoformat(raw.metadata["ingested_at"])
    except KeyError as exc:
        raise CbarError(f"{raw.source_file}: missing metadata {exc.args[0]!r}") from exc
    meta_rate_date = raw.metadata.get("rate_date")
    if meta_rate_date and date.fromisoformat(meta_rate_date) != parsed.rate_date:
        raise CbarError(
            f"{raw.source_file}: file date {parsed.rate_date} "
            f"!= metadata rate_date {meta_rate_date}"
        )

    loaded_at = loaded_at or datetime.now(UTC)
    quantum = Decimal(1).scaleb(-RATE_TYPE.scale)
    n = len(parsed.rates)
    columns = {
        "requested_date": [requested_date] * n,
        "rate_date": [parsed.rate_date] * n,
        "currency_code": [r.code for r in parsed.rates],
        "currency_name": [r.name for r in parsed.rates],
        "nominal": [r.nominal for r in parsed.rates],
        "rate_azn": [r.rate_azn.quantize(quantum) for r in parsed.rates],
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
    log_event(log, "cbar.bronze_loaded", **result)
    return result
