"""config/cities.yaml -> bronze.config_cities (SQLite catalog, offline)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.config import DEFAULT_CITIES_FILE, CityConfigError
from ingestion.sources.config_cities import BRONZE_TABLE, load, to_arrow


def test_to_arrow_has_all_cities_and_the_file_hash() -> None:
    batch = to_arrow(DEFAULT_CITIES_FILE, "run-1")

    assert batch.num_rows == 10
    assert set(batch["currency"].to_pylist()) >= {"EUR", "USD", "AZN", "AED"}
    digest = hashlib.sha256(DEFAULT_CITIES_FILE.read_bytes()).hexdigest()
    assert batch["_source_file"][0].as_py() == f"config/cities.yaml sha256={digest}"


def test_load_replaces_the_whole_table(
    catalog: SqlCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load("run-a", catalog=catalog)
    assert catalog.load_table(BRONZE_TABLE).scan().to_arrow().num_rows == 10

    # A smaller file: the table must equal the file, not accumulate rows.
    cities = yaml.safe_load(DEFAULT_CITIES_FILE.read_text())["cities"][:3]
    small = tmp_path / "cities.yaml"
    small.write_text(yaml.safe_dump({"cities": cities}, sort_keys=False))
    monkeypatch.setenv("WANDERLAKE_CITIES_FILE", str(small))
    load("run-b", catalog=catalog)

    rows = catalog.load_table(BRONZE_TABLE).scan().to_arrow().to_pylist()
    assert len(rows) == 3
    assert {r["_dag_run_id"] for r in rows} == {"run-b"}


def test_invalid_file_writes_nothing(
    catalog: SqlCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "cities.yaml"
    bad.write_text("cities:\n  - id: Bad Id\n")
    monkeypatch.setenv("WANDERLAKE_CITIES_FILE", str(bad))

    with pytest.raises(CityConfigError):
        load("run-x", catalog=catalog)
    assert not catalog.table_exists(BRONZE_TABLE)
