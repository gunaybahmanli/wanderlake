"""REST Countries v5 raw -> bronze, offline (real v5 records as fixture, moto, SQLite catalog)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import requests_mock as rm
from conftest import BUCKET
from pyiceberg.catalog.sql import SqlCatalog

import ingestion.sources.rest_countries as rc
from ingestion.common.http import build_session
from ingestion.sources.rest_countries import (
    BASE_URL,
    BRONZE_ARROW_SCHEMA,
    BRONZE_TABLE,
    RestCountriesError,
    api_session,
    ingest_snapshot,
    load_bronze,
    parse_page,
    records_to_arrow,
    validate_snapshot,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "rest_countries_page.json").read_bytes()
RECORDS = json.loads(FIXTURE)["data"]["objects"]  # MO (no capital), NL, PS (2 capitals), Abkhazia
SNAPSHOT = date(2026, 11, 16)
CITIES = {"amsterdam": "NL"}
DEPRECATED = (
    b'{"success": false, "data": null, "errors": [{"message": "This API version has been '
    b'deprecated. Please visit https://restcountries.com/docs/countries/legacy-api-deprecation"}]}'
)


@pytest.fixture(autouse=True)
def small_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "MIN_COUNTRIES", 3)  # the fixture has 3 coded countries, not 200


def session() -> Any:
    s = build_session(backoff_factor=0)
    s.headers["Authorization"] = "Bearer test-key"
    return s


def by_code(table: Any) -> dict[str, dict[str, Any]]:
    return {r["cca2"]: r for r in table.to_pylist()}


# --- parsing and validation -----------------------------------------------------------------------


def test_parse_page_returns_records_and_meta() -> None:
    records, meta = parse_page(FIXTURE)

    assert len(records) == 4
    assert meta["total"] == 4


def test_deprecated_v3_response_is_rejected() -> None:
    with pytest.raises(RestCountriesError, match="not a v5 country list"):
        parse_page(DEPRECATED)


def test_entities_without_iso_code_are_left_out() -> None:
    coded, uncoded = validate_snapshot(RECORDS)

    assert sorted(r["codes"]["alpha_2"] for r in coded) == ["MO", "NL", "PS"]
    assert uncoded == ["Abkhazia"]


def test_malformed_code_fails_loudly() -> None:
    bad = json.loads(json.dumps(RECORDS))
    bad[1]["codes"]["alpha_2"] = "nl"

    with pytest.raises(RestCountriesError, match="malformed alpha_2 codes: Netherlands='nl'"):
        validate_snapshot(bad)


def test_duplicate_code_fails_loudly() -> None:
    with pytest.raises(RestCountriesError, match="duplicate alpha_2 codes: NL"):
        validate_snapshot([*RECORDS, RECORDS[1]])


def test_too_few_countries_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "MIN_COUNTRIES", 200)

    with pytest.raises(RestCountriesError, match="only 3 countries with an ISO code"):
        validate_snapshot(RECORDS)


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(RestCountriesError, match="REST_COUNTRIES_API_KEY"):
        api_session(env={})


# --- bronze columns -----------------------------------------------------------------------------


def test_records_to_arrow_columns_and_json() -> None:
    coded, _ = validate_snapshot(RECORDS)
    batch = records_to_arrow(
        [(r, "s3://raw/x/page=1.json") for r in coded],
        SNAPSHOT,
        datetime(2026, 11, 16, 3, 0, tzinfo=UTC),
        "run-1",
    )
    rows = by_code(batch)

    assert batch.schema == BRONZE_ARROW_SCHEMA
    nl = rows["NL"]
    assert (nl["cca3"], nl["name_common"], nl["capital"]) == ("NLD", "Netherlands", "Amsterdam")
    assert nl["region"] == "Europe"
    assert json.loads(nl["currencies_json"])[0]["code"] == "EUR"
    assert json.loads(nl["languages_json"])[0]["name"] == "Dutch"
    assert isinstance(json.loads(nl["timezones_json"]), list)
    assert nl["un_member"] is True and nl["independent"] is True
    assert nl["flag_png_url"].endswith("/nl.png")
    assert isinstance(nl["population"], int) and isinstance(nl["area_km2"], float)


def test_two_capitals_keep_all_in_json() -> None:
    coded, _ = validate_snapshot(RECORDS)
    rows = by_code(records_to_arrow([(r, "s") for r in coded], SNAPSHOT, datetime.now(UTC), None))

    ps = rows["PS"]
    capitals = json.loads(ps["capitals_json"])
    assert [c["name"] for c in capitals] == ["Ramallah", "Jerusalem"]
    primary = [c["name"] for c in capitals if c.get("attributes", {}).get("primary")]
    assert ps["capital"] == (primary[0] if primary else "Ramallah")


def test_no_capital_is_null_with_empty_json() -> None:
    coded, _ = validate_snapshot(RECORDS)
    rows = by_code(records_to_arrow([(r, "s") for r in coded], SNAPSHOT, datetime.now(UTC), None))

    assert rows["MO"]["capital"] is None
    assert rows["MO"]["capitals_json"] == "[]"


# --- raw and idempotent reload ------------------------------------------------------------------


def test_ingest_stores_pages_unchanged_with_metadata(requests_mock: rm.Mocker, s3: Any) -> None:
    requests_mock.get(BASE_URL, content=FIXTURE)

    result = ingest_snapshot(SNAPSHOT, session=session(), s3=s3)

    assert (result["pages"], result["countries"], result["without_iso_code"]) == (1, 3, 1)
    obj = s3.get_object(
        Bucket=BUCKET, Key="rest_countries/all/snapshot_date=2026-11-16/page=1.json"
    )
    assert obj["Body"].read() == FIXTURE
    meta = obj["Metadata"]
    assert meta["snapshot_date"] == "2026-11-16"
    assert (meta["record_count"], meta["page"], meta["page_count"], meta["total"]) == (
        "4",
        "1",
        "1",
        "4",
    )
    assert {"ingested_at", "source_url", "http_status"} <= set(meta)
    assert "test-key" not in meta["source_url"]  # the key travels in a header only
    assert requests_mock.last_request.headers["Authorization"] == "Bearer test-key"


def test_rerun_removes_stale_pages(requests_mock: rm.Mocker, s3: Any) -> None:
    stale = "rest_countries/all/snapshot_date=2026-11-16/page=7.json"
    s3.put_object(Bucket=BUCKET, Key=stale, Body=b"{}")
    requests_mock.get(BASE_URL, content=FIXTURE)

    ingest_snapshot(SNAPSHOT, session=session(), s3=s3)

    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert stale not in keys


def test_reload_of_the_same_snapshot_keeps_one_set_of_rows(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    requests_mock.get(BASE_URL, content=FIXTURE)
    ingest_snapshot(SNAPSHOT, session=session(), s3=s3)
    ingest_snapshot(date(2026, 11, 9), session=session(), s3=s3)

    load_bronze(SNAPSHOT, "run-a", catalog=catalog, s3=s3, cities_country_codes=CITIES)
    load_bronze(date(2026, 11, 9), "run-b", catalog=catalog, s3=s3, cities_country_codes=CITIES)
    load_bronze(SNAPSHOT, "run-c", catalog=catalog, s3=s3, cities_country_codes=CITIES)  # reload

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow().to_pylist()
    assert len(rows) == 6  # 2 snapshots x 3 coded countries (Abkhazia is not loaded)
    assert {r["_dag_run_id"] for r in rows if r["snapshot_date"] == SNAPSHOT} == {"run-c"}
    assert {r["_dag_run_id"] for r in rows if r["snapshot_date"] == date(2026, 11, 9)} == {"run-b"}


def test_missing_city_country_fails_before_writing(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    requests_mock.get(BASE_URL, content=FIXTURE)
    ingest_snapshot(SNAPSHOT, session=session(), s3=s3)

    with pytest.raises(RestCountriesError, match=r"missing from the snapshot: baku \(AZ\)"):
        load_bronze(
            SNAPSHOT,
            "run-x",
            catalog=catalog,
            s3=s3,
            cities_country_codes={"baku": "AZ", "amsterdam": "NL"},
        )
    assert not catalog.table_exists(BRONZE_TABLE)  # nothing was written
