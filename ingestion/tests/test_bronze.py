"""Raw -> bronze for Frankfurter, on a local PyIceberg SqlCatalog (SQLite + temp warehouse)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from conftest import BUCKET, ecb_body
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.common.iceberg import catalog_properties, ensure_table, get_catalog, replace_rows
from ingestion.sources.frankfurter import (
    BRONZE_ARROW_SCHEMA,
    BRONZE_PARTITION_SPEC,
    BRONZE_SCHEMA,
    BRONZE_TABLE,
    FrankfurterError,
    RawObject,
    build_key,
    load_bronze,
    raw_to_arrow,
)

LOADED_AT = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def raw_object(requested: str, rate_date: str, **meta: str) -> RawObject:
    metadata = {
        "requested_date": requested,
        "rate_date": rate_date,
        "ingested_at": "2026-11-19T10:00:00+00:00",
        "source_url": "https://api.frankfurter.dev/v2/providers/ecb/rates",
        "http_status": "200",
        **meta,
    }
    return RawObject(
        body=ecb_body(rate_date),
        metadata=metadata,
        source_file=f"s3://raw/{build_key(date.fromisoformat(requested))}",
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[SqlCatalog]:
    env = {
        "ICEBERG_CATALOG_NAME": "wanderlake",
        "ICEBERG_WAREHOUSE": f"file://{tmp_path / 'warehouse'}",
        "ICEBERG_CATALOG_URI": f"sqlite:///{tmp_path / 'catalog.db'}",
        "ICEBERG_INIT_CATALOG_TABLES": "true",
    }
    cat = get_catalog(env)
    cat.create_namespace("bronze")
    yield cat


def put_raw(s3: Any, requested: str, rate_date: str) -> None:
    s3.put_object(
        Bucket=BUCKET,
        Key=build_key(date.fromisoformat(requested)),
        Body=ecb_body(rate_date),
        ContentType="application/json",
        Metadata={
            "requested_date": requested,
            "rate_date": rate_date,
            "ingested_at": "2026-11-19T10:00:00+00:00",
            "source_url": "https://api.frankfurter.dev/v2/providers/ecb/rates",
            "http_status": "200",
        },
    )


# --- raw -> Arrow --------------------------------------------------------------------------------


def test_raw_to_arrow_one_row_per_quote_currency() -> None:
    batch = raw_to_arrow(raw_object("2026-11-17", "2026-11-17"), "run-1", loaded_at=LOADED_AT)

    assert batch.schema == BRONZE_ARROW_SCHEMA
    assert batch.num_rows == 3
    assert batch["quote_currency"].to_pylist() == ["USD", "GBP", "JPY"]
    assert set(batch["base_currency"].to_pylist()) == {"EUR"}
    assert set(batch["_dag_run_id"].to_pylist()) == {"run-1"}
    assert set(batch["_loaded_at"].to_pylist()) == {LOADED_AT}
    assert set(batch["_ingested_at"].to_pylist()) == {datetime(2026, 11, 19, 10, 0, tzinfo=UTC)}
    assert set(batch["_source_file"].to_pylist()) == {
        "s3://raw/frankfurter/rates/date=2026-11-17/rates.json"
    }


def test_rates_are_exact_decimals() -> None:
    batch = raw_to_arrow(raw_object("2026-11-17", "2026-11-17"), None, loaded_at=LOADED_AT)

    assert pa.types.is_decimal(batch.schema.field("rate").type)
    rates = dict(zip(batch["quote_currency"].to_pylist(), batch["rate"].to_pylist(), strict=True))
    assert rates["GBP"] == Decimal("0.83559")
    assert rates["JPY"] == Decimal("164.12")


def test_weekend_keeps_requested_date_and_last_business_day() -> None:
    batch = raw_to_arrow(raw_object("2026-11-14", "2026-11-13"), None, loaded_at=LOADED_AT)

    assert set(batch["requested_date"].to_pylist()) == {date(2026, 11, 14)}
    assert set(batch["rate_date"].to_pylist()) == {date(2026, 11, 13)}


def test_metadata_rate_date_mismatch_is_rejected() -> None:
    raw = raw_object("2026-11-17", "2026-11-17")
    raw.metadata["rate_date"] = "2026-11-16"  # body says 2026-11-17

    with pytest.raises(FrankfurterError, match="metadata rate_date"):
        raw_to_arrow(raw, None)


def test_missing_metadata_is_rejected() -> None:
    raw = raw_object("2026-11-17", "2026-11-17")
    del raw.metadata["ingested_at"]

    with pytest.raises(FrankfurterError, match="missing metadata 'ingested_at'"):
        raw_to_arrow(raw, None)


# --- Iceberg -------------------------------------------------------------------------------------


def test_loading_the_same_date_twice_keeps_one_set_of_rows(s3, catalog: SqlCatalog) -> None:
    put_raw(s3, "2026-11-14", "2026-11-13")
    put_raw(s3, "2026-11-17", "2026-11-17")

    load_bronze(date(2026, 11, 14), "run-a", catalog=catalog, s3=s3)
    load_bronze(date(2026, 11, 17), "run-b", catalog=catalog, s3=s3)
    load_bronze(date(2026, 11, 14), "run-c", catalog=catalog, s3=s3)  # reload

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow()
    by_date: dict[date, list[str]] = {}
    for d, run in zip(
        rows["requested_date"].to_pylist(), rows["_dag_run_id"].to_pylist(), strict=True
    ):
        by_date.setdefault(d, []).append(run)

    assert rows.num_rows == 6
    assert by_date[date(2026, 11, 14)] == ["run-c"] * 3  # replaced, not duplicated
    assert by_date[date(2026, 11, 17)] == ["run-b"] * 3  # other date untouched


def test_ensure_table_returns_existing_table(catalog: SqlCatalog) -> None:
    first = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    second = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)

    assert first.metadata.table_uuid == second.metadata.table_uuid
    assert [f.name for f in second.spec().fields] == ["requested_month"]


def test_replace_rows_rejects_rows_for_another_value(catalog: SqlCatalog) -> None:
    table = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, BRONZE_PARTITION_SPEC, catalog=catalog)
    batch = raw_to_arrow(raw_object("2026-11-17", "2026-11-17"), None, loaded_at=LOADED_AT)

    with pytest.raises(ValueError, match="every row must have requested_date == '2026-11-18'"):
        replace_rows(table, batch, "requested_date", "2026-11-18")


# --- configuration --------------------------------------------------------------------------------


def test_catalog_properties_come_from_env() -> None:
    props = catalog_properties(
        {
            "ICEBERG_CATALOG_NAME": "wanderlake",
            "ICEBERG_WAREHOUSE": "s3://lakehouse/warehouse",
            "ICEBERG_CATALOG_DB_USER": "iceberg",
            "ICEBERG_CATALOG_DB_PASSWORD": "p@ss/word",
            "S3_ENDPOINT": "http://seaweedfs:8333",
            "S3_ACCESS_KEY": "a",
            "S3_SECRET_KEY": "b",
        }
    )

    assert (
        props["uri"] == "postgresql+psycopg2://iceberg:p%40ss%2Fword@postgres:5432/iceberg_catalog"
    )
    assert props["warehouse"] == "s3://lakehouse/warehouse"
    assert props["s3.endpoint"] == "http://seaweedfs:8333"
    assert props["s3.force-virtual-addressing"] == "false"
    assert props["init_catalog_tables"] == "false"


def test_catalog_name_comes_from_env(tmp_path: Path) -> None:
    cat = get_catalog(
        {
            "ICEBERG_CATALOG_NAME": "wanderlake",
            "ICEBERG_WAREHOUSE": f"file://{tmp_path}",
            "ICEBERG_CATALOG_URI": f"sqlite:///{tmp_path / 'c.db'}",
            "ICEBERG_INIT_CATALOG_TABLES": "true",
        }
    )
    assert cat.name == "wanderlake"
