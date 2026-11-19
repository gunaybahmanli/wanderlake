"""FCDO travel advice raw -> bronze, offline (trimmed real GOV.UK responses, moto, SQLite)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import requests_mock as rm
from conftest import BUCKET
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.common.http import build_session
from ingestion.sources.fcdo import (
    BRONZE_TABLE,
    COUNTRY_URL,
    INDEX_URL,
    FcdoError,
    ingest_snapshot,
    load_bronze,
    normalize,
    parse_advice,
    resolve_slugs,
)

FIXTURES = Path(__file__).parent / "fixtures"
INDEX = json.loads((FIXTURES / "fcdo_index.json").read_bytes())
THAILAND = (FIXTURES / "fcdo_thailand.json").read_bytes()  # alert: parts of the country
JAPAN = (FIXTURES / "fcdo_japan.json").read_bytes()  # no alert
NAMES = {"TH": ["Thailand", "Kingdom of Thailand"], "JP": ["Japan", "Japan"]}
SNAPSHOT = date(2026, 11, 20)


@pytest.fixture
def gov_uk(requests_mock: rm.Mocker) -> rm.Mocker:
    requests_mock.get(INDEX_URL, json=INDEX)
    requests_mock.get(COUNTRY_URL.format(slug="thailand"), content=THAILAND)
    requests_mock.get(COUNTRY_URL.format(slug="japan"), content=JAPAN)
    return requests_mock


def ingest(s3: Any, snapshot: date = SNAPSHOT) -> dict[str, Any]:
    return ingest_snapshot(
        snapshot,
        country_codes=["JP", "TH"],
        country_names=NAMES,
        session=build_session(backoff_factor=0),
        s3=s3,
        pause_seconds=0,
    )


# --- mapping ------------------------------------------------------------------------------------


def test_normalize() -> None:
    assert normalize("The Netherlands") == "netherlands"
    assert normalize("Côte d'Ivoire") == "cote d ivoire"


def test_resolves_by_name_and_by_synonym() -> None:
    mapping = resolve_slugs(
        {
            "US": ["United States", "United States of America"],
            "CZ": ["Czechia"],
            "TH": ["Thailand"],
        },
        INDEX,
    )

    assert mapping == {"CZ": "czechia", "TH": "thailand", "US": "usa"}  # US via a synonym


def test_unknown_country_fails() -> None:
    with pytest.raises(FcdoError, match=r"XX \(Atlantis\): no FCDO country"):
        resolve_slugs({"TH": ["Thailand"], "XX": ["Atlantis"]}, INDEX)


def test_ambiguous_name_fails() -> None:
    # FCDO synonyms include cities; a name matching two countries must not pick one silently.
    index = json.loads(json.dumps(INDEX))
    for child in index["links"]["children"]:
        if child["details"]["country"]["slug"] == "japan":
            child["details"]["country"]["synonyms"] = ["Thailand"]
    with pytest.raises(FcdoError, match=r"TH \(Thailand\): ambiguous \['japan', 'thailand'\]"):
        resolve_slugs({"TH": ["Thailand"]}, index)


def test_not_an_index_fails() -> None:
    with pytest.raises(FcdoError, match="not a travel advice index"):
        resolve_slugs({"TH": ["Thailand"]}, {"document_type": "guide"})


# --- parsing ------------------------------------------------------------------------------------


def test_parse_advice_with_alert() -> None:
    doc = parse_advice(THAILAND)

    assert doc["title"] == "Thailand travel advice"
    assert doc["details"]["alert_status"] == ["avoid_all_but_essential_travel_to_parts"]
    assert [p["slug"] for p in doc["details"]["parts"]][2] == "safety-and-security"


def test_not_a_travel_advice_page_fails() -> None:
    with pytest.raises(FcdoError, match="not a travel advice page"):
        parse_advice(b'{"document_type": "guide", "details": {}}')


# --- raw and bronze -----------------------------------------------------------------------------


def test_ingest_stores_each_country_and_the_index(gov_uk: rm.Mocker, s3: Any) -> None:
    result = ingest(s3)

    assert result["countries"] == 2
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"])
    prefix = "fcdo/advice/snapshot_date=2026-11-20/"
    assert keys == [prefix + "JP.json", prefix + "TH.json", prefix + "_index.json"]
    obj = s3.get_object(Bucket=BUCKET, Key=prefix + "TH.json")
    assert obj["Body"].read() == THAILAND
    meta = obj["Metadata"]
    assert (meta["country_code"], meta["slug"], meta["snapshot_date"]) == (
        "TH",
        "thailand",
        "2026-11-20",
    )
    assert {"ingested_at", "source_url", "http_status", "public_updated_at"} <= set(meta)


def test_bronze_rows_keep_html_and_alerts(gov_uk: rm.Mocker, s3: Any, catalog: SqlCatalog) -> None:
    ingest(s3)
    load_bronze(SNAPSHOT, "run-1", country_codes=["JP", "TH"], catalog=catalog, s3=s3)

    rows = {
        r["country_code"]: r for r in catalog.load_table(BRONZE_TABLE).scan().to_arrow().to_pylist()
    }
    th, jp = rows["TH"], rows["JP"]
    assert json.loads(th["alert_status_json"]) == ["avoid_all_but_essential_travel_to_parts"]
    assert json.loads(jp["alert_status_json"]) == []
    parts = json.loads(th["parts_json"])
    source_parts = json.loads(THAILAND)["details"]["parts"]
    assert [p["body"] for p in parts] == [p["body"] for p in source_parts]  # HTML unchanged
    assert parts[0]["body"].lstrip().startswith("<div")  # HTML, leading newline kept as received
    published = json.loads(THAILAND)["public_updated_at"]
    assert th["public_updated_at"] == datetime.fromisoformat(published).astimezone(UTC)
    assert th["public_updated_at"].utcoffset() == timedelta(0)
    assert th["change_description"]


def test_reload_of_the_same_snapshot_keeps_one_row_per_country(
    gov_uk: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    ingest(s3)
    ingest(s3, date(2026, 11, 19))
    load_bronze(SNAPSHOT, "run-a", country_codes=["JP", "TH"], catalog=catalog, s3=s3)
    load_bronze(date(2026, 11, 19), "run-b", country_codes=["JP", "TH"], catalog=catalog, s3=s3)
    load_bronze(SNAPSHOT, "run-c", country_codes=["JP", "TH"], catalog=catalog, s3=s3)  # reload

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow().to_pylist()
    assert len(rows) == 4  # 2 snapshots x 2 countries
    assert {r["_dag_run_id"] for r in rows if r["snapshot_date"] == SNAPSHOT} == {"run-c"}


def test_missing_country_file_fails(gov_uk: rm.Mocker, s3: Any, catalog: SqlCatalog) -> None:
    ingest(s3)

    with pytest.raises(FcdoError, match=r"no raw advice for \['AZ'\]"):
        load_bronze(SNAPSHOT, None, country_codes=["AZ", "JP", "TH"], catalog=catalog, s3=s3)


def test_rerun_removes_files_of_an_earlier_mapping(gov_uk: rm.Mocker, s3: Any) -> None:
    stale = "fcdo/advice/snapshot_date=2026-11-20/ZZ.json"
    s3.put_object(Bucket=BUCKET, Key=stale, Body=b"{}")

    ingest(s3)

    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert stale not in keys


def test_offset_timestamp_is_parsed() -> None:
    # public_updated_at comes with a UTC offset, e.g. +01:00
    assert datetime.fromisoformat("2026-10-12T10:20:34+01:00").tzinfo == timezone(
        timedelta(hours=1)
    )
