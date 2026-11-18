from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import requests
import requests_mock as rm
from conftest import BUCKET, ecb_body

from ingestion.common.http import build_session
from ingestion.sources.frankfurter import (
    BASE_URL,
    RATES_PATH,
    FrankfurterError,
    build_key,
    fetch_rates,
    ingest_rates,
)

URL = BASE_URL + RATES_PATH


@pytest.fixture
def session() -> requests.Session:
    # Real retry config, but no sleeping between attempts.
    return build_session(backoff_factor=0)


# --- key builder -------------------------------------------------------------


def test_build_key_uses_requested_date() -> None:
    assert build_key(date(2026, 11, 14)) == "frankfurter/rates/date=2026-11-14/rates.json"


def test_build_key_zero_pads() -> None:
    assert build_key(date(2026, 1, 5)) == "frankfurter/rates/date=2026-01-05/rates.json"


# --- fetch -------------------------------------------------------------------


def test_fetch_weekday_returns_raw_body(requests_mock: rm.Mocker, session) -> None:
    body = ecb_body("2026-11-17")
    requests_mock.get(URL, content=body)

    result = fetch_rates(date(2026, 11, 17), session=session)

    assert result.body == body
    assert result.http_status == 200
    assert result.rate_date == date(2026, 11, 17)
    assert result.rows == 3
    assert requests_mock.last_request.qs == {"date": ["2026-11-17"], "base": ["eur"]}


def test_fetch_weekend_returns_last_business_day(requests_mock: rm.Mocker, session) -> None:
    requests_mock.get(URL, content=ecb_body("2026-11-13"))  # Friday

    result = fetch_rates(date(2026, 11, 14), session=session)  # Saturday

    assert result.requested_date == date(2026, 11, 14)
    assert result.rate_date == date(2026, 11, 13)


def test_fetch_empty_list_raises(requests_mock: rm.Mocker, session) -> None:
    requests_mock.get(URL, content=b"[]")

    with pytest.raises(FrankfurterError, match="no rates available"):
        fetch_rates(date(2026, 12, 25), session=session)


def test_fetch_non_list_raises(requests_mock: rm.Mocker, session) -> None:
    requests_mock.get(URL, json={"message": "unexpected"})

    with pytest.raises(FrankfurterError, match="expected a JSON list"):
        fetch_rates(date(2026, 11, 17), session=session)


def test_fetch_retries_on_503_and_429_then_succeeds(http_server, session) -> None:
    http_server.script = [(503, b""), (429, b""), (200, ecb_body("2026-11-17"))]

    result = fetch_rates(date(2026, 11, 17), session=session, base_url=http_server.url)

    assert result.http_status == 200
    assert result.rate_date == date(2026, 11, 17)
    assert http_server.calls == 3


def test_fetch_gives_up_after_retries(http_server) -> None:
    http_server.script = [(503, b"")]
    session = build_session(total_retries=2, backoff_factor=0)

    with pytest.raises(requests.HTTPError):
        fetch_rates(date(2026, 11, 17), session=session, base_url=http_server.url)
    assert http_server.calls == 3  # first attempt + 2 retries


def test_fetch_404_is_not_retried(requests_mock: rm.Mocker, session) -> None:
    requests_mock.get(URL, status_code=404, json={"message": "not found"})

    with pytest.raises(requests.HTTPError):
        fetch_rates(date(2026, 11, 17), session=session)
    assert requests_mock.call_count == 1


# --- ingest (fetch + S3 write) -------------------------------------------------


def test_ingest_stores_body_unchanged_with_metadata(requests_mock: rm.Mocker, session, s3) -> None:
    body = ecb_body("2026-11-13")
    requests_mock.get(URL, content=body)

    before = datetime.now(UTC).replace(microsecond=0)
    result = ingest_rates(date(2026, 11, 14), bucket=BUCKET, session=session)

    key = "frankfurter/rates/date=2026-11-14/rates.json"
    assert result["key"] == key
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    assert obj["Body"].read() == body
    assert obj["ContentType"] == "application/json"

    meta = obj["Metadata"]
    assert set(meta) == {"ingested_at", "source_url", "http_status", "requested_date", "rate_date"}
    assert meta["requested_date"] == "2026-11-14"
    assert meta["rate_date"] == "2026-11-13"
    assert meta["http_status"] == "200"
    assert meta["source_url"].startswith(URL + "?")
    assert datetime.fromisoformat(meta["ingested_at"]) >= before


def test_ingest_rerun_overwrites_same_key(requests_mock: rm.Mocker, session, s3) -> None:
    requests_mock.get(
        URL, [{"content": ecb_body("2026-11-17")}, {"content": ecb_body("2026-11-18")}]
    )

    ingest_rates(date(2026, 11, 18), bucket=BUCKET, session=session)  # before ECB publishes
    ingest_rates(date(2026, 11, 18), bucket=BUCKET, session=session)  # after

    listed = s3.list_objects_v2(Bucket=BUCKET)["Contents"]
    assert [o["Key"] for o in listed] == ["frankfurter/rates/date=2026-11-18/rates.json"]
    obj = s3.get_object(Bucket=BUCKET, Key=listed[0]["Key"])
    assert obj["Metadata"]["rate_date"] == "2026-11-18"
