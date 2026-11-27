"""Open-Meteo forecast and history raw -> bronze, offline (trimmed real responses, moto, SQLite)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests_mock as rm
from conftest import BUCKET
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.common.http import build_session
from ingestion.common.iceberg import ensure_table, replace_range
from ingestion.config import load_cities
from ingestion.sources.open_meteo import (
    ARCHIVE_URL,
    FORECAST_TABLE,
    FORECAST_URL,
    HISTORY_SCHEMA,
    HISTORY_SPEC,
    HISTORY_TABLE,
    OpenMeteoError,
    OpenMeteoUnavailableError,
    forecast_to_arrow,
    history_to_arrow,
    history_window,
    ingest_forecast,
    ingest_history,
    load_forecast,
    load_history,
    location_params,
    month_ranges,
    parse_locations,
)

FIXTURES = Path(__file__).parent / "fixtures"
FORECAST = (FIXTURES / "open_meteo_forecast.json").read_bytes()  # baku, madrid, tokyo; 7 days
HISTORY = (FIXTURES / "open_meteo_history.json").read_bytes()  # same cities; 2026-11-08..10
CITIES = load_cities()[:3]
NOW = datetime(2026, 11, 20, 5, 0, tzinfo=UTC)


def session() -> Any:
    return build_session(backoff_factor=0)


def history_body(start: date, days: int, shift: float = 0.0) -> bytes:
    """A history response for start..start+days-1, built from the real fixture's locations."""
    locations = json.loads(HISTORY)
    for loc in locations:
        daily = loc["daily"]
        template = {k: v[0] for k, v in daily.items() if k != "time"}
        loc["daily"] = {"time": [(start + timedelta(days=i)).isoformat() for i in range(days)]}
        for k, v in template.items():
            value = v + shift if isinstance(v, float) else v
            loc["daily"][k] = [value] * days
    return json.dumps(locations).encode()


# --- requests and parsing -------------------------------------------------------------------------


def test_location_params_keep_config_order_and_timezones() -> None:
    params = location_params(CITIES)

    assert params["timezone"] == "Asia/Baku,Europe/Madrid,Asia/Tokyo"
    assert params["latitude"].split(",")[0] == str(CITIES[0].latitude)


def test_forecast_parses_several_cities() -> None:
    batch = forecast_to_arrow(FORECAST, CITIES, date(2026, 11, 20), NOW, "s3://raw/f", "run-1")

    assert batch.num_rows == 21  # 3 cities x 7 days
    assert sorted(set(batch["city_id"].to_pylist())) == ["baku", "madrid", "tokyo"]
    row = batch.to_pylist()[0]
    assert (row["city_id"], row["run_date"]) == ("baku", date(2026, 11, 20))
    assert isinstance(row["weather_code"], int)
    assert {"uv_index_max", "precipitation_probability_max", "temperature_2m_mean"} <= set(row)


def test_history_parses_several_cities() -> None:
    batch = history_to_arrow(
        HISTORY, CITIES, date(2026, 11, 8), date(2026, 11, 10), NOW, "s3://raw/h", None
    )

    assert batch.num_rows == 9  # 3 cities x 3 days
    assert set(batch["weather_date"].to_pylist()) == {
        date(2026, 11, 8),
        date(2026, 11, 9),
        date(2026, 11, 10),
    }
    assert "uv_index_max" not in batch.column_names  # forecast-only variable


@pytest.mark.parametrize(
    "body",
    [
        b"<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head></html>",
        b"",
        b'[{"latitude": 40.4, "daily": {"time": [',  # cut off mid-response
    ],
)
def test_a_response_that_is_not_json_is_temporary(body: bytes) -> None:
    from ingestion.pipeline import is_transient

    with pytest.raises(OpenMeteoUnavailableError, match="response is not valid JSON") as caught:
        parse_locations(body, load_cities())
    assert is_transient(caught.value)  # retried by the runner (2026-11-26 failed without retries)
    with pytest.raises(OpenMeteoError) as data_error:  # valid JSON, wrong content: not retried
        parse_locations(FORECAST, load_cities()[:4])
    assert not is_transient(data_error.value)


def test_missing_city_is_detected() -> None:
    with pytest.raises(OpenMeteoError, match="expected 4 locations, got 3"):
        parse_locations(FORECAST, load_cities()[:4])


def test_wrong_city_order_is_detected() -> None:
    swapped = [CITIES[1], CITIES[0], CITIES[2]]

    with pytest.raises(OpenMeteoError, match="madrid: timezone 'Asia/Baku' != config"):
        parse_locations(FORECAST, swapped)


def test_far_away_grid_point_is_detected() -> None:
    body = json.loads(FORECAST)
    body[0]["latitude"] += 1.0  # about 110 km
    with pytest.raises(OpenMeteoError, match="baku: returned grid point is .* km away"):
        parse_locations(json.dumps(body).encode(), CITIES)


def test_error_object_is_reported() -> None:
    body = b'{"error":true,"reason":"Parameter \'end_date\' is out of allowed range"}'

    with pytest.raises(OpenMeteoError, match="end_date' is out of allowed range"):
        parse_locations(body, CITIES)


def test_missing_day_is_detected() -> None:
    with pytest.raises(OpenMeteoError, match="baku: expected days 2026-11-08..2026-11-11"):
        history_to_arrow(HISTORY, CITIES, date(2026, 11, 8), date(2026, 11, 11), NOW, "s", None)


# --- windows and months ---------------------------------------------------------------------------


def test_history_window_is_the_seven_days_before_run_date() -> None:
    assert history_window(date(2026, 11, 20)) == (date(2026, 11, 13), date(2026, 11, 19))


def test_month_ranges_are_clipped() -> None:
    months = month_ranges(date(2023, 1, 15), date(2023, 3, 10))

    assert months == [
        ("2023-01", date(2023, 1, 15), date(2023, 1, 31)),
        ("2023-02", date(2023, 2, 1), date(2023, 2, 28)),
        ("2023-03", date(2023, 3, 1), date(2023, 3, 10)),
    ]


# --- raw and idempotent reloads -------------------------------------------------------------------


def test_forecast_raw_metadata_and_idempotent_reload(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    requests_mock.get(FORECAST_URL, content=FORECAST)
    run = date(2026, 11, 20)
    ingest_forecast(run, cities=CITIES, session=session(), s3=s3)

    obj = s3.get_object(Bucket=BUCKET, Key="open_meteo/forecast/run_date=2026-11-20/forecast.json")
    assert obj["Body"].read() == FORECAST
    meta = obj["Metadata"]
    assert (meta["city_ids"], meta["record_count"], meta["run_date"]) == (
        "baku,madrid,tokyo",
        "21",
        "2026-11-20",
    )

    load_forecast(run, "run-a", catalog=catalog, s3=s3)
    load_forecast(run, "run-b", catalog=catalog, s3=s3)  # reload

    rows = catalog.load_table(FORECAST_TABLE).scan().to_arrow().to_pylist()
    assert len(rows) == 21
    assert {r["_dag_run_id"] for r in rows} == {"run-b"}


def test_history_overlapping_reloads_keep_one_row_per_city_and_day(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    # Two overlapping daily windows (like consecutive daily runs), then the first one again.
    requests_mock.get(ARCHIVE_URL, content=history_body(date(2026, 11, 1), 7))
    ingest_history("h/1.json", date(2026, 11, 1), date(2026, 11, 7), cities=CITIES, s3=s3)
    requests_mock.get(ARCHIVE_URL, content=history_body(date(2026, 11, 3), 7, shift=1.0))
    ingest_history("h/2.json", date(2026, 11, 3), date(2026, 11, 9), cities=CITIES, s3=s3)

    load_history("h/1.json", "run-1", catalog=catalog, s3=s3)
    load_history("h/2.json", "run-2", catalog=catalog, s3=s3)  # replaces 11-03..11-09
    load_history("h/2.json", "run-3", catalog=catalog, s3=s3)  # same batch again

    rows = catalog.load_table(HISTORY_TABLE).scan().to_arrow().to_pylist()
    keys = [(r["city_id"], r["weather_date"]) for r in rows]
    assert len(keys) == len(set(keys)) == 27  # 3 cities x 9 days, no duplicates
    by_day = {r["weather_date"]: r["_dag_run_id"] for r in rows if r["city_id"] == "baku"}
    assert by_day[date(2026, 11, 2)] == "run-1"  # outside the second window: kept
    assert by_day[date(2026, 11, 5)] == "run-3"  # inside: replaced


def test_replace_range_rejects_rows_outside_the_range(catalog: SqlCatalog) -> None:
    table = ensure_table(HISTORY_TABLE, HISTORY_SCHEMA, HISTORY_SPEC, catalog=catalog)
    batch = history_to_arrow(HISTORY, CITIES, date(2026, 11, 8), date(2026, 11, 10), NOW, "s", None)

    with pytest.raises(ValueError, match="every row must have '2026-11-09' <= weather_date"):
        replace_range(table, batch, "weather_date", "2026-11-09", "2026-11-10")


def test_raw_cities_missing_from_config_fail(s3: Any, catalog: SqlCatalog) -> None:
    s3.put_object(
        Bucket=BUCKET,
        Key="h/x.json",
        Body=HISTORY,
        Metadata={
            "city_ids": "baku,atlantis,tokyo",
            "ingested_at": NOW.isoformat(),
            "start_date": "2026-11-08",
            "end_date": "2026-11-10",
        },
    )
    with pytest.raises(OpenMeteoError, match=r"cities not in config any more: \['atlantis'\]"):
        load_history("h/x.json", None, catalog=catalog, s3=s3)
