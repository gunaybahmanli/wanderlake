"""GeoNames cities15000 raw zip -> bronze, offline (fixture TSV zipped in tests, moto, SQLite)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
import requests_mock as rm
from conftest import BUCKET
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.common.http import build_session
from ingestion.sources.geonames import (
    BRONZE_TABLE,
    SOURCE_URL,
    GeoNamesError,
    build_key,
    check_city_ids,
    haversine_km,
    ingest_snapshot,
    load_bronze,
    match_city,
    parse_cities,
    validate,
)

TSV = (Path(__file__).parent / "fixtures" / "geonames_cities15000.tsv").read_bytes()
SNAPSHOT = date(2026, 11, 1)
CITY_IDS = {"baku": 587084, "dubai": 292223}


def zipped(tsv: bytes = TSV, member: str = "cities15000.txt") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, tsv)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def small_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    import ingestion.sources.geonames as g

    monkeypatch.setattr(g, "MIN_ROWS", 4)  # the fixture has 5 rows, not 20,000+


def by_id(table: pa.Table) -> dict[int, dict[str, Any]]:
    return {r["geonameid"]: r for r in table.to_pylist()}


# --- parsing, types and nulls ---------------------------------------------------------------------


def test_parse_types_and_values() -> None:
    table = parse_cities(zipped())
    baku = by_id(table)[587084]

    assert table.num_rows == 5
    assert table.schema.field("geonameid").type == pa.int64()
    assert table.schema.field("population").type == pa.int64()
    assert table.schema.field("elevation").type == pa.int32()
    assert table.schema.field("modification_date").type == pa.date32()
    assert baku["name"] == "Baku" and baku["alternatenames"] == "Bakou,Baku,Bakı"
    assert baku["admin1_code"] == "09"  # leading zero kept: codes are strings
    assert (baku["latitude"], baku["dem"], baku["timezone"]) == (40.37767, -26, "Asia/Baku")
    assert baku["modification_date"] == date(2026, 4, 12)


def test_empty_values_are_null() -> None:
    baku = by_id(parse_cities(zipped()))[587084]

    assert baku["elevation"] is None
    assert baku["cc2"] is None
    assert baku["admin2_code"] is None


def test_quote_in_name_is_kept() -> None:
    assert by_id(parse_cities(zipped()))[9000001]["name"] == 'Qasr "Old" Town'


def test_not_a_zip_is_rejected() -> None:
    with pytest.raises(GeoNamesError, match="not a zip"):
        parse_cities(b"<html>maintenance</html>")


def test_wrong_member_is_rejected() -> None:
    with pytest.raises(GeoNamesError, match="cities15000.txt not in the zip"):
        parse_cities(zipped(member="other.txt"))


# --- validation ----------------------------------------------------------------------------------


def test_too_few_rows_fails() -> None:
    with pytest.raises(GeoNamesError, match="only 5 rows"):
        validate(parse_cities(zipped()), min_rows=20_000)


def test_duplicate_geonameid_fails() -> None:
    first_line = TSV.splitlines(keepends=True)[0]
    with pytest.raises(GeoNamesError, match="geonameid is not unique"):
        validate(parse_cities(zipped(TSV + first_line)))


def test_latitude_out_of_range_fails() -> None:
    bad = TSV.replace(b"\t40.37767\t", b"\t95.0\t", 1)
    with pytest.raises(GeoNamesError, match="latitude: 0 null, 1 out of range"):
        validate(parse_cities(zipped(bad)))


# --- matching ------------------------------------------------------------------------------------


def test_haversine_known_distance() -> None:
    # Madrid -> Rome is about 1,365 km.
    assert 1350 < haversine_km(40.4169, -3.7033, 41.8931, 12.4828) < 1380


def test_match_prefers_ppl_codes_then_distance() -> None:
    table = parse_cities(zipped())
    # The PPLX place is closer, but the PPLA one is preferred.
    match = match_city("springfield", "US", 39.79, -89.60, table)

    assert (match.geonameid, match.feature_code, match.candidates) == (9000002, "PPLA", 2)


def test_match_dubai_like_config() -> None:
    match = match_city("Dubai", "AE", 25.2697, 55.3094, parse_cities(zipped()))

    assert match.geonameid == 292223
    assert 21 < match.distance_km < 22


def test_match_too_far_fails() -> None:
    with pytest.raises(GeoNamesError, match="closest match is .* km away"):
        match_city("Baku", "AZ", 41.5, 49.9, parse_cities(zipped()))  # about 125 km off


def test_match_unknown_name_fails() -> None:
    with pytest.raises(GeoNamesError, match="no GeoNames place with that name"):
        match_city("Atlantis", "AZ", 40.4, 49.9, parse_cities(zipped()))


def test_check_city_ids() -> None:
    check_city_ids({587084, 292223}, CITY_IDS)
    with pytest.raises(GeoNamesError, match=r"missing from the snapshot: dubai \(292223\)"):
        check_city_ids({587084}, CITY_IDS)


# --- raw and idempotent reload ------------------------------------------------------------------


def test_ingest_stores_zip_unchanged_with_metadata(requests_mock: rm.Mocker, s3: Any) -> None:
    body = zipped()
    requests_mock.get(SOURCE_URL, content=body, headers={"Last-Modified": "Sun, 01 Nov 2026"})

    ingest_snapshot(SNAPSHOT, session=build_session(backoff_factor=0), s3=s3)

    obj = s3.get_object(Bucket=BUCKET, Key=build_key(SNAPSHOT))
    assert obj["Body"].read() == body
    assert obj["ContentType"] == "application/zip"
    meta = obj["Metadata"]
    assert meta["record_count"] == "5"
    assert meta["file_size"] == str(len(body))
    assert meta["sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["snapshot_date"] == "2026-11-01"
    assert meta["last_modified"] == "Sun, 01 Nov 2026"


def test_reload_of_the_same_snapshot_keeps_one_set_of_rows(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    requests_mock.get(SOURCE_URL, content=zipped())
    session = build_session(backoff_factor=0)
    ingest_snapshot(SNAPSHOT, session=session, s3=s3)
    ingest_snapshot(date(2026, 10, 1), session=session, s3=s3)

    load_bronze(SNAPSHOT, "run-a", catalog=catalog, s3=s3, city_ids=CITY_IDS)
    load_bronze(date(2026, 10, 1), "run-b", catalog=catalog, s3=s3, city_ids=CITY_IDS)
    load_bronze(SNAPSHOT, "run-c", catalog=catalog, s3=s3, city_ids=CITY_IDS)  # reload

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow().to_pylist()
    assert len(rows) == 10  # 2 snapshots x 5 places
    assert {r["_dag_run_id"] for r in rows if r["snapshot_date"] == SNAPSHOT} == {"run-c"}
    assert {r["_dag_run_id"] for r in rows if r["snapshot_date"] == date(2026, 10, 1)} == {"run-b"}


def test_tampered_raw_zip_is_rejected(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    requests_mock.get(SOURCE_URL, content=zipped())
    ingest_snapshot(SNAPSHOT, session=build_session(backoff_factor=0), s3=s3)
    key = build_key(SNAPSHOT)
    meta = s3.head_object(Bucket=BUCKET, Key=key)["Metadata"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=zipped(TSV.replace(b"Baku", b"Bako")), Metadata=meta)

    with pytest.raises(GeoNamesError, match="sha256 does not match"):
        load_bronze(SNAPSHOT, "run-x", catalog=catalog, s3=s3, city_ids=CITY_IDS)


def test_missing_city_id_fails_before_writing(
    requests_mock: rm.Mocker, s3: Any, catalog: SqlCatalog
) -> None:
    requests_mock.get(SOURCE_URL, content=zipped())
    ingest_snapshot(SNAPSHOT, session=build_session(backoff_factor=0), s3=s3)

    with pytest.raises(GeoNamesError, match=r"seoul \(1835848\)"):
        load_bronze(SNAPSHOT, "run-x", catalog=catalog, s3=s3, city_ids={"seoul": 1835848})
    assert not catalog.table_exists(BRONZE_TABLE)
