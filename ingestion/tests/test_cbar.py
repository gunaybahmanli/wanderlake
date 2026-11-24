"""CBAR raw XML -> bronze, fully offline (fixture XML, requests-mock, moto, SQLite catalog)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import requests_mock as rm
from conftest import BUCKET
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.common.http import build_session
from ingestion.sources.cbar import (
    BRONZE_ARROW_SCHEMA,
    BRONZE_TABLE,
    CbarError,
    CbarUnavailableError,
    RawObject,
    build_key,
    build_url,
    fetch_rates,
    ingest_rates,
    load_bronze,
    load_bronze_range,
    parse_rates,
    raw_to_arrow,
    today_in_baku,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "cbar_2026-11-13.xml").read_bytes()
LOADED_AT = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 19)


def raw_object(requested: str, body: bytes = FIXTURE, **meta: str) -> RawObject:
    metadata = {
        "requested_date": requested,
        "rate_date": "2026-11-13",
        "ingested_at": "2026-11-19T10:00:00+00:00",
        "source_url": build_url(date.fromisoformat(requested)),
        "http_status": "200",
        **meta,
    }
    return RawObject(body, metadata, f"s3://raw/{build_key(date.fromisoformat(requested))}")


# --- parsing --------------------------------------------------------------------------------------


def test_parse_keeps_only_foreign_currencies() -> None:
    parsed = parse_rates(FIXTURE)

    assert parsed.rate_date == date(2026, 11, 13)
    assert [r.code for r in parsed.rates] == ["USD", "AED", "JPY", "KRW"]
    assert parsed.skipped == ["XAU", "XAG", "SDR"]  # metals and SDR


def test_nominal_and_exact_rates() -> None:
    rates = {r.code: r for r in parse_rates(FIXTURE).rates}

    assert (rates["USD"].nominal, rates["USD"].rate_azn) == (1, Decimal("1.7"))
    assert (rates["JPY"].nominal, rates["JPY"].rate_azn) == (100, Decimal("1.0653"))
    assert (rates["KRW"].nominal, rates["KRW"].rate_azn) == (100, Decimal("0.1276"))
    assert rates["AED"].name == "1 BƏƏ dirhəmi"  # names kept as published


def test_unexpected_nominal_is_rejected() -> None:
    body = FIXTURE.replace(b"<Nominal>100</Nominal>", b"<Nominal>100 units</Nominal>", 1)

    with pytest.raises(CbarError, match="JPY: unexpected nominal '100 units'"):
        parse_rates(body)


def test_invalid_xml_is_rejected() -> None:
    with pytest.raises(CbarError, match="invalid XML"):
        parse_rates(b"<ValCurs Date='13.11.2026'><ValType>")


def test_entity_expansion_is_blocked() -> None:
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>
<ValCurs Date="13.11.2026"><ValType Type="Xarici valyutalar">&b;</ValType></ValCurs>"""

    with pytest.raises(Exception, match="(?i)entit"):  # defusedxml refuses entity declarations
        parse_rates(bomb)


def test_file_without_currencies_is_rejected() -> None:
    body = b'<ValCurs Date="13.11.2026"><ValType Type="Bank metallar\xc4\xb1"></ValType></ValCurs>'

    with pytest.raises(CbarError, match="no foreign currencies"):
        parse_rates(body)


# --- fetch and raw ------------------------------------------------------------------------------


def test_build_url_and_key() -> None:
    assert build_url(date(2026, 11, 3)) == "https://www.cbar.az/currencies/03.11.2026.xml"
    assert build_key(date(2026, 11, 3)) == "cbar/rates/date=2026-11-03/rates.xml"


def test_future_date_is_refused_without_calling_cbar(requests_mock: rm.Mocker) -> None:
    with pytest.raises(CbarError, match="in the future"):
        fetch_rates(date(2026, 11, 20), session=build_session(backoff_factor=0), today=TODAY)
    assert requests_mock.call_count == 0


def test_today_in_baku_uses_baku_time() -> None:
    # 21:30 UTC on 19 Nov is already 01:30 on 20 Nov in Baku (UTC+4).
    assert today_in_baku(datetime(2026, 11, 19, 21, 30, tzinfo=UTC)) == date(2026, 11, 20)


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"<!DOCTYPE html><html><head><title>503</title></head></html>", "application/xml"),
        (b"\n  <html><body>Please wait</body></html>", ""),
        (b"<p>busy</p>", "text/html; charset=utf-8"),
    ],
)
def test_a_web_page_instead_of_xml_is_temporary(
    requests_mock: rm.Mocker, body: bytes, content_type: str
) -> None:
    from ingestion.pipeline import is_transient

    url = build_url(date(2026, 11, 13))
    requests_mock.get(url, content=body, headers={"Content-Type": content_type})
    with pytest.raises(CbarUnavailableError, match="web page instead of XML") as caught:
        fetch_rates(date(2026, 11, 13), session=build_session(backoff_factor=0), today=TODAY)
    assert is_transient(caught.value)  # retried by the runner, unlike malformed XML
    assert not is_transient(CbarError("invalid XML: mismatched tag"))


def test_weekend_fetch_keeps_last_business_day(requests_mock: rm.Mocker) -> None:
    requests_mock.get(build_url(date(2026, 11, 14)), content=FIXTURE)

    result = fetch_rates(date(2026, 11, 14), session=build_session(backoff_factor=0), today=TODAY)

    assert (result.requested_date, result.rate_date) == (date(2026, 11, 14), date(2026, 11, 13))
    assert result.rows == 4


def test_ingest_stores_body_unchanged_with_metadata(requests_mock: rm.Mocker, s3: Any) -> None:
    requests_mock.get(build_url(date(2026, 11, 14)), content=FIXTURE)

    ingest_rates(date(2026, 11, 14), session=build_session(backoff_factor=0), today=TODAY)

    obj = s3.get_object(Bucket=BUCKET, Key="cbar/rates/date=2026-11-14/rates.xml")
    assert obj["Body"].read() == FIXTURE
    assert obj["ContentType"] == "application/xml"
    meta = obj["Metadata"]
    assert set(meta) == {"ingested_at", "source_url", "http_status", "requested_date", "rate_date"}
    assert (meta["requested_date"], meta["rate_date"]) == ("2026-11-14", "2026-11-13")


# --- bronze -------------------------------------------------------------------------------------


def test_raw_to_arrow_rows() -> None:
    batch = raw_to_arrow(raw_object("2026-11-14"), "run-1", loaded_at=LOADED_AT)

    assert batch.schema == BRONZE_ARROW_SCHEMA
    assert batch["currency_code"].to_pylist() == ["USD", "AED", "JPY", "KRW"]
    assert batch["nominal"].to_pylist() == [1, 1, 100, 100]
    assert batch["rate_azn"].to_pylist()[2] == Decimal("1.065300")
    assert set(batch["rate_date"].to_pylist()) == {date(2026, 11, 13)}
    assert set(batch["requested_date"].to_pylist()) == {date(2026, 11, 14)}


def test_metadata_rate_date_mismatch_is_rejected() -> None:
    with pytest.raises(CbarError, match="metadata rate_date"):
        raw_to_arrow(raw_object("2026-11-14", rate_date="2026-11-12"), None)


def test_reload_of_the_same_date_keeps_one_set_of_rows(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    for day in ("2026-11-14", "2026-11-15"):
        requests_mock.get(build_url(date.fromisoformat(day)), content=FIXTURE)
        ingest_rates(date.fromisoformat(day), session=build_session(backoff_factor=0), today=TODAY)

    load_bronze(date(2026, 11, 14), "run-a", catalog=catalog, s3=s3)
    load_bronze(date(2026, 11, 15), "run-b", catalog=catalog, s3=s3)
    load_bronze(date(2026, 11, 14), "run-c", catalog=catalog, s3=s3)  # reload

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow()
    pairs = list(
        zip(rows["requested_date"].to_pylist(), rows["_dag_run_id"].to_pylist(), strict=True)
    )

    assert rows.num_rows == 8  # 2 dates x 4 currencies
    assert sorted({run for d, run in pairs if d == date(2026, 11, 14)}) == ["run-c"]
    assert sorted({run for d, run in pairs if d == date(2026, 11, 15)}) == ["run-b"]


def test_range_load_replaces_the_range_in_one_commit(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    for day in ("2026-11-14", "2026-11-15"):
        requests_mock.get(build_url(date.fromisoformat(day)), content=FIXTURE)
        ingest_rates(date.fromisoformat(day), session=build_session(backoff_factor=0), today=TODAY)
    load_bronze(date(2026, 11, 14), "daily", catalog=catalog, s3=s3)

    result = load_bronze_range(
        date(2026, 11, 13), date(2026, 11, 15), "backfill", catalog=catalog, s3=s3
    )

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow()
    assert result["rows"] == rows.num_rows == 8  # 2 dates x 4 currencies
    assert result["missing_raw"] == "2026-11-13"
    assert set(rows["_dag_run_id"].to_pylist()) == {"backfill"}
