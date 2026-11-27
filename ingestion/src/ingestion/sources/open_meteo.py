"""Open-Meteo: daily weather forecast and history for the configured cities.

Sources (free API, non-commercial use: < 10,000 calls/day, 5,000/hour, 600/minute):
    forecast  GET https://api.open-meteo.com/v1/forecast        (forecast_days=7)
    history   GET https://archive-api.open-meteo.com/v1/archive (start_date, end_date)
One request covers all cities: latitude, longitude and timezone are comma-separated lists, and
the response is a list in request order (no ids; coordinates are snapped to the model grid).
A call counts fractionally: per location, per block of 10 variables and 14 days.

Archive: ERA5/ERA5-Land reanalysis (from 1940, "daily with 5 days delay"), with the latest days
filled from the ECMWF IFS model. Recent values are provisional and get revised, which is why the
daily run re-fetches the last 7 days. end_date after today (UTC) is rejected with HTTP 400.

Layers:
    raw     s3://raw/open_meteo/forecast/run_date=YYYY-MM-DD/forecast.json
            s3://raw/open_meteo/history/date=YYYY-MM-DD/history.json      (daily, last 7 days)
            s3://raw/open_meteo/history/backfill/month=YYYY-MM/history.json (one-time backfill)
    bronze  iceberg.bronze.open_meteo_forecast, iceberg.bronze.open_meteo_history

License: CC BY 4.0. Weather data by Open-Meteo.com (https://open-meteo.com/).
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa
import requests
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import MonthTransform, YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    IntegerType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion.common.geo import haversine_km
from ingestion.common.http import SourceUnavailableError, build_session
from ingestion.common.iceberg import ensure_table, replace_range, replace_rows
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_json, s3_client
from ingestion.config import City, load_cities

log = get_logger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
RAW_BUCKET = "raw"
FORECAST_TABLE = "bronze.open_meteo_forecast"
HISTORY_TABLE = "bronze.open_meteo_history"
FORECAST_DAYS = 7
HISTORY_WINDOW_DAYS = 7  # the daily run re-fetches run_date-7 .. run_date-1
MAX_GRID_KM = 30.0  # returned grid point vs our coordinates (ERA5 is 0.25 degrees)
BACKFILL_PAUSE_SECONDS = 6.0  # about 220 weighted calls a minute, far below 600

FORECAST_KEY = "open_meteo/forecast/run_date={date}/forecast.json"
HISTORY_KEY = "open_meteo/history/date={date}/history.json"
BACKFILL_KEY = "open_meteo/history/backfill/month={month}/history.json"

# Daily variables (metric defaults: degC, mm, km/h, cm, seconds). name -> Arrow type.
COMMON_VARIABLES: list[tuple[str, pa.DataType]] = [
    ("weather_code", pa.int32()),  # WMO code
    ("temperature_2m_max", pa.float64()),
    ("temperature_2m_min", pa.float64()),
    ("temperature_2m_mean", pa.float64()),
    ("precipitation_sum", pa.float64()),
    ("rain_sum", pa.float64()),
    ("snowfall_sum", pa.float64()),
    ("precipitation_hours", pa.float64()),
    ("wind_speed_10m_max", pa.float64()),
    ("sunshine_duration", pa.float64()),  # seconds
]
FORECAST_ONLY: list[tuple[str, pa.DataType]] = [
    ("uv_index_max", pa.float64()),
    ("precipitation_probability_max", pa.float64()),  # percent
]
FORECAST_VARIABLES = COMMON_VARIABLES + FORECAST_ONLY

_ICEBERG = {pa.int32(): IntegerType(), pa.float64(): DoubleType()}
_TS = pa.timestamp("us", tz="UTC")
_INGESTION_ARROW = [
    pa.field("_ingested_at", _TS, nullable=False),
    pa.field("_loaded_at", _TS, nullable=False),
    pa.field("_source_file", pa.string(), nullable=False),
    pa.field("_dag_run_id", pa.string()),
]


def _iceberg_schema(
    keys: list[tuple[str, Any]], variables: list[tuple[str, pa.DataType]]
) -> Schema:
    fields = [
        NestedField(i, name, typ, required=True) for i, (name, typ) in enumerate(keys, start=1)
    ]
    n = len(fields)
    fields += [
        NestedField(n + i, name, _ICEBERG[typ], required=False)
        for i, (name, typ) in enumerate(variables, start=1)
    ]
    n = len(fields)
    fields += [
        NestedField(n + 1, "_ingested_at", TimestamptzType(), required=True),
        NestedField(n + 2, "_loaded_at", TimestamptzType(), required=True),
        NestedField(n + 3, "_source_file", StringType(), required=True),
        NestedField(n + 4, "_dag_run_id", StringType(), required=False),
    ]
    return Schema(*fields)


FORECAST_SCHEMA = _iceberg_schema(
    [("run_date", DateType()), ("city_id", StringType()), ("forecast_date", DateType())],
    FORECAST_VARIABLES,
)
HISTORY_SCHEMA = _iceberg_schema(
    [("city_id", StringType()), ("weather_date", DateType())], COMMON_VARIABLES
)
# Forecast: 70 rows a run, monthly partitions. History: 3,650 rows a year, yearly partitions.
FORECAST_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=MonthTransform(), name="run_month")
)
HISTORY_SPEC = PartitionSpec(
    PartitionField(source_id=2, field_id=1000, transform=YearTransform(), name="weather_year")
)


class OpenMeteoError(RuntimeError):
    """Raised when a response is unusable or does not match the configured cities."""


class OpenMeteoUnavailableError(OpenMeteoError, SourceUnavailableError):
    """Open-Meteo answered with something that is not JSON (seen on 2026-11-26 from a GitHub
    runner: HTTP 200 with a non-JSON body). Temporary, so it is retried like the CBAR web page;
    the body is never stored in raw. Valid JSON that does not match the cities stays a data error.
    """


@dataclass(frozen=True)
class RawResponse:
    body: bytes  # exactly as received
    source_url: str
    http_status: int


# --- requests -----------------------------------------------------------------------------------


def location_params(cities: Sequence[City]) -> dict[str, str]:
    """All cities in one request, each in its own timezone (order = config order)."""
    return {
        "latitude": ",".join(f"{c.latitude}" for c in cities),
        "longitude": ",".join(f"{c.longitude}" for c in cities),
        "timezone": ",".join(c.timezone for c in cities),
    }


def _get(url: str, params: dict[str, Any], session: requests.Session | None) -> RawResponse:
    session = session or build_session()
    response = session.get(url, params=params)
    if response.status_code == 400:  # Open-Meteo explains the problem in the body
        raise OpenMeteoError(f"HTTP 400 from {url}: {response.text[:300]}")
    response.raise_for_status()
    return RawResponse(response.content, response.url, response.status_code)


def fetch_forecast(cities: Sequence[City], session: requests.Session | None = None) -> RawResponse:
    params = {
        **location_params(cities),
        "daily": ",".join(n for n, _ in FORECAST_VARIABLES),
        "forecast_days": FORECAST_DAYS,
    }
    return _get(FORECAST_URL, params, session)


def fetch_history(
    cities: Sequence[City], start: date, end: date, session: requests.Session | None = None
) -> RawResponse:
    if end < start:
        raise OpenMeteoError(f"end {end} is before start {start}")
    params = {
        **location_params(cities),
        "daily": ",".join(n for n, _ in COMMON_VARIABLES),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return _get(ARCHIVE_URL, params, session)


# --- parsing and validation -----------------------------------------------------------------------


def parse_locations(
    body: bytes, cities: Sequence[City], check_location: bool = True
) -> list[dict[str, Any]]:
    """Return one location object per city, in config order. Fails loudly on any mismatch."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise OpenMeteoUnavailableError("response is not valid JSON") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise OpenMeteoError(f"Open-Meteo error: {payload.get('reason')}")
    locations = [payload] if isinstance(payload, dict) else payload
    if not isinstance(locations, list) or len(locations) != len(cities):
        got = len(locations) if isinstance(locations, list) else type(locations).__name__
        raise OpenMeteoError(f"expected {len(cities)} locations, got {got}")
    for city, loc in zip(cities, locations, strict=True):
        if not isinstance(loc, dict) or "daily" not in loc:
            raise OpenMeteoError(f"{city.id}: location has no daily data")
        if check_location:
            if loc.get("timezone") != city.timezone:
                raise OpenMeteoError(
                    f"{city.id}: timezone {loc.get('timezone')!r} != config {city.timezone!r}"
                )
            km = haversine_km(city.latitude, city.longitude, loc["latitude"], loc["longitude"])
            if km > MAX_GRID_KM:
                raise OpenMeteoError(f"{city.id}: returned grid point is {km:.1f} km away")
    return locations


def _rows(
    location: dict[str, Any],
    city_id: str,
    variables: list[tuple[str, pa.DataType]],
    date_column: str,
    expected_dates: list[date] | None,
) -> list[dict[str, Any]]:
    daily = location["daily"]
    days = [date.fromisoformat(t) for t in daily.get("time", [])]
    if expected_dates is not None and days != expected_dates:
        raise OpenMeteoError(
            f"{city_id}: expected days {expected_dates[0]}..{expected_dates[-1]}, got "
            f"{days[0] if days else None}..{days[-1] if days else None} ({len(days)} days)"
        )
    missing = [name for name, _ in variables if name not in daily]
    if missing:
        raise OpenMeteoError(f"{city_id}: missing daily variables {missing}")
    rows = []
    for i, day in enumerate(days):
        row: dict[str, Any] = {"city_id": city_id, date_column: day}
        for name, typ in variables:
            value = daily[name][i]
            row[name] = int(value) if value is not None and typ == pa.int32() else value
        rows.append(row)
    return rows


def _arrow(
    rows: list[dict[str, Any]],
    keys: list[pa.Field],
    variables: list[tuple[str, pa.DataType]],
    ingested_at: datetime,
    source_file: str,
    dag_run_id: str | None,
    loaded_at: datetime | None,
) -> pa.Table:
    loaded_at = loaded_at or datetime.now(UTC)
    for r in rows:
        r.update(
            _ingested_at=ingested_at,
            _loaded_at=loaded_at,
            _source_file=source_file,
            _dag_run_id=dag_run_id,
        )
    schema = pa.schema(keys + [pa.field(n, t) for n, t in variables] + _INGESTION_ARROW)
    return pa.Table.from_pylist(rows, schema=schema)


def forecast_to_arrow(
    body: bytes,
    cities: Sequence[City],
    run_date: date,
    ingested_at: datetime,
    source_file: str,
    dag_run_id: str | None,
    loaded_at: datetime | None = None,
    check_location: bool = True,
) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for city, loc in zip(cities, parse_locations(body, cities, check_location), strict=True):
        city_rows = _rows(loc, city.id, FORECAST_VARIABLES, "forecast_date", None)
        if len(city_rows) != FORECAST_DAYS:
            raise OpenMeteoError(f"{city.id}: expected {FORECAST_DAYS} days, got {len(city_rows)}")
        for r in city_rows:
            r["run_date"] = run_date
        rows += city_rows
    keys = [
        pa.field("run_date", pa.date32(), nullable=False),
        pa.field("city_id", pa.string(), nullable=False),
        pa.field("forecast_date", pa.date32(), nullable=False),
    ]
    return _arrow(rows, keys, FORECAST_VARIABLES, ingested_at, source_file, dag_run_id, loaded_at)


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def history_to_arrow(
    body: bytes,
    cities: Sequence[City],
    start: date,
    end: date,
    ingested_at: datetime,
    source_file: str,
    dag_run_id: str | None,
    loaded_at: datetime | None = None,
    check_location: bool = True,
) -> pa.Table:
    expected = date_range(start, end)
    rows: list[dict[str, Any]] = []
    for city, loc in zip(cities, parse_locations(body, cities, check_location), strict=True):
        rows += _rows(loc, city.id, COMMON_VARIABLES, "weather_date", expected)
    keys = [
        pa.field("city_id", pa.string(), nullable=False),
        pa.field("weather_date", pa.date32(), nullable=False),
    ]
    return _arrow(rows, keys, COMMON_VARIABLES, ingested_at, source_file, dag_run_id, loaded_at)


# --- raw ----------------------------------------------------------------------------------------


def _store(
    key: str,
    raw: RawResponse,
    cities: Sequence[City],
    extra: dict[str, str],
    bucket: str,
    s3: Any | None,
) -> dict[str, str]:
    locations = parse_locations(raw.body, cities)  # validate before storing
    days = len(locations[0]["daily"].get("time", []))
    metadata = {
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": raw.source_url,
        "http_status": str(raw.http_status),
        **extra,
        "city_ids": ",".join(c.id for c in cities),  # response order: maps raw back to cities
        "record_count": str(len(locations) * days),
    }
    put_json(bucket, key, raw.body, metadata=metadata, client=s3)
    log_event(log, "open_meteo.ingested", bucket=bucket, key=key, **metadata)
    return {"bucket": bucket, "key": key, **metadata}


def ingest_forecast(
    run_date: date,
    bucket: str = RAW_BUCKET,
    cities: Sequence[City] | None = None,
    session: requests.Session | None = None,
    s3: Any | None = None,
) -> dict[str, str]:
    cities = list(cities or load_cities())
    raw = fetch_forecast(cities, session=session)
    key = FORECAST_KEY.format(date=run_date.isoformat())
    return _store(key, raw, cities, {"run_date": run_date.isoformat()}, bucket, s3)


def history_window(run_date: date) -> tuple[date, date]:
    """The daily run's history window: the 7 days before run_date."""
    return run_date - timedelta(days=HISTORY_WINDOW_DAYS), run_date - timedelta(days=1)


def ingest_history(
    key: str,
    start: date,
    end: date,
    bucket: str = RAW_BUCKET,
    cities: Sequence[City] | None = None,
    session: requests.Session | None = None,
    s3: Any | None = None,
) -> dict[str, str]:
    cities = list(cities or load_cities())
    raw = fetch_history(cities, start, end, session=session)
    extra = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    return _store(key, raw, cities, extra, bucket, s3)


def ingest_recent_history(run_date: date, bucket: str = RAW_BUCKET, **kwargs: Any) -> dict:
    start, end = history_window(run_date)
    key = HISTORY_KEY.format(date=run_date.isoformat())
    return ingest_history(key, start, end, bucket=bucket, **kwargs)


# --- raw -> bronze ------------------------------------------------------------------------------


def _read(bucket: str, key: str, s3: Any | None) -> tuple[bytes, dict[str, str], list[City]]:
    """Raw body, metadata, and the cities in response order (from city_ids in the metadata)."""
    s3 = s3 or s3_client()
    obj = s3.get_object(Bucket=bucket, Key=key)
    meta = {k.lower(): v for k, v in obj.get("Metadata", {}).items()}
    by_id = {c.id: c for c in load_cities()}
    ids = meta.get("city_ids", "").split(",")
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise OpenMeteoError(f"s3://{bucket}/{key}: cities not in config any more: {unknown}")
    return obj["Body"].read(), meta, [by_id[i] for i in ids]


def load_forecast(
    run_date: date,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    catalog: Any | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    key = FORECAST_KEY.format(date=run_date.isoformat())
    body, meta, cities = _read(bucket, key, s3)
    batch = forecast_to_arrow(
        body,
        cities,
        run_date,
        datetime.fromisoformat(meta["ingested_at"]),
        f"s3://{bucket}/{key}",
        dag_run_id,
    )
    table = ensure_table(FORECAST_TABLE, FORECAST_SCHEMA, FORECAST_SPEC, catalog=catalog)
    rows = replace_rows(table, batch, "run_date", run_date.isoformat())
    result = {"table": FORECAST_TABLE, "run_date": run_date.isoformat(), "rows": rows}
    log_event(log, "open_meteo.forecast_loaded", **result)
    return result


def load_history(
    key: str,
    dag_run_id: str | None,
    bucket: str = RAW_BUCKET,
    catalog: Any | None = None,
    s3: Any | None = None,
) -> dict[str, Any]:
    body, meta, cities = _read(bucket, key, s3)
    start, end = date.fromisoformat(meta["start_date"]), date.fromisoformat(meta["end_date"])
    batch = history_to_arrow(
        body,
        cities,
        start,
        end,
        datetime.fromisoformat(meta["ingested_at"]),
        f"s3://{bucket}/{key}",
        dag_run_id,
    )
    table = ensure_table(HISTORY_TABLE, HISTORY_SCHEMA, HISTORY_SPEC, catalog=catalog)
    rows = replace_range(table, batch, "weather_date", start.isoformat(), end.isoformat())
    result = {
        "table": HISTORY_TABLE,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": rows,
    }
    log_event(log, "open_meteo.history_loaded", **result)
    return result


def load_recent_history(run_date: date, dag_run_id: str | None, **kwargs: Any) -> dict[str, Any]:
    return load_history(HISTORY_KEY.format(date=run_date.isoformat()), dag_run_id, **kwargs)


# --- one-time backfill --------------------------------------------------------------------------


def month_ranges(start: date, end: date) -> list[tuple[str, date, date]]:
    """Split start..end into calendar months: (YYYY-MM, first day, last day), clipped."""
    ranges = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        last = min(end, next_month - timedelta(days=1))
        ranges.append((cursor.strftime("%Y-%m"), cursor, last))
        cursor = next_month
    return ranges


def backfill_history(
    start: date,
    end: date,
    pause_seconds: float = BACKFILL_PAUSE_SECONDS,
    bucket: str = RAW_BUCKET,
    session: requests.Session | None = None,
    s3: Any | None = None,
    catalog: Any | None = None,
) -> list[dict[str, Any]]:
    """Fetch, store and load history month by month, pausing between requests. Idempotent."""
    cities = load_cities()
    session = session or build_session()
    results = []
    months = month_ranges(start, end)
    for i, (month, first, last) in enumerate(months, start=1):
        key = BACKFILL_KEY.format(month=month)
        ingest_history(key, first, last, bucket=bucket, cities=cities, session=session, s3=s3)
        loaded = load_history(key, dag_run_id="backfill", bucket=bucket, catalog=catalog, s3=s3)
        log_event(log, "open_meteo.backfill_month", month=month, done=f"{i}/{len(months)}")
        results.append(loaded)
        if i < len(months) and pause_seconds:
            time.sleep(pause_seconds)
    return results


def main(argv: Sequence[str] | None = None) -> None:
    import logging

    parser = argparse.ArgumentParser(description="Open-Meteo history backfill (month by month)")
    sub = parser.add_subparsers(dest="command", required=True)
    bf = sub.add_parser("backfill")
    bf.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    bf.add_argument(
        "--end",
        type=date.fromisoformat,
        help="default: 8 days ago (the daily run covers the last 7 days)",
    )
    bf.add_argument("--pause", type=float, default=BACKFILL_PAUSE_SECONDS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    end = args.end or datetime.now(UTC).date() - timedelta(days=HISTORY_WINDOW_DAYS + 1)
    results = backfill_history(args.start, end, pause_seconds=args.pause)
    total = sum(r["rows"] for r in results)
    print(f"backfill done: {len(results)} months, {total} rows, {args.start}..{end}")


if __name__ == "__main__":
    main()
