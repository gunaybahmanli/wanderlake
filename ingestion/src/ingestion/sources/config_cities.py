"""Copy config/cities.yaml (the single source of truth for cities) into bronze.config_cities.

The YAML stays the source of truth; the table only makes it available to SQL (dbt sources, Trino).
Each run validates the file (ingestion.config.load_cities) and replaces the whole table: the
table always equals the current file. There is no raw copy in S3, because the file is versioned
in git; `_source_file` records its path and sha256.

    python -m ingestion.sources.config_cities          (also: make config-load, DAG config_cities)
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from ingestion.common.iceberg import ensure_table
from ingestion.common.logging import get_logger, log_event
from ingestion.config import CITIES_FILE_ENV, DEFAULT_CITIES_FILE, load_cities

log = get_logger(__name__)

BRONZE_TABLE = "bronze.config_cities"

BRONZE_SCHEMA = Schema(
    NestedField(1, "city_id", StringType(), required=True, doc="Slug, unique"),
    NestedField(2, "name", StringType(), required=True),
    NestedField(3, "country_code", StringType(), required=True, doc="ISO 3166-1 alpha-2"),
    NestedField(4, "timezone", StringType(), required=True, doc="IANA time zone"),
    NestedField(5, "latitude", DoubleType(), required=True),
    NestedField(6, "longitude", DoubleType(), required=True),
    NestedField(7, "currency", StringType(), required=True, doc="ISO 4217"),
    NestedField(8, "rate_source", StringType(), required=True, doc="ecb or cbar"),
    NestedField(9, "inside_airbnb", BooleanType(), required=False),
    NestedField(10, "geonames_id", LongType(), required=True),
    NestedField(11, "_ingested_at", TimestamptzType(), required=True, doc="File read"),
    NestedField(12, "_loaded_at", TimestamptzType(), required=True, doc="Row written"),
    NestedField(13, "_source_file", StringType(), required=True, doc="Path and sha256 of the file"),
    NestedField(14, "_dag_run_id", StringType(), required=False),
)


def cities_file() -> Path:
    return Path(os.environ.get(CITIES_FILE_ENV) or DEFAULT_CITIES_FILE)


def to_arrow(path: Path, dag_run_id: str | None, now: datetime | None = None) -> pa.Table:
    """Validated cities as bronze rows."""
    now = now or datetime.now(UTC)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    source = f"config/{path.name} sha256={digest}"
    rows = [
        {
            "city_id": c.id,
            "name": c.name,
            "country_code": c.country_code,
            "timezone": c.timezone,
            "latitude": c.latitude,
            "longitude": c.longitude,
            "currency": c.currency,
            "rate_source": c.rate_source,
            "inside_airbnb": c.inside_airbnb,
            "geonames_id": c.geonames_id,
            "_ingested_at": now,
            "_loaded_at": now,
            "_source_file": source,
            "_dag_run_id": dag_run_id,
        }
        for c in load_cities(path)
    ]
    return pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA.as_arrow())


def load(dag_run_id: str | None = None, catalog: Any | None = None) -> dict[str, Any]:
    """Validate config/cities.yaml and replace the whole table with it (idempotent)."""
    path = cities_file()
    batch = to_arrow(path, dag_run_id)
    table = ensure_table(BRONZE_TABLE, BRONZE_SCHEMA, PartitionSpec(), catalog=catalog)
    table.overwrite(batch, snapshot_properties={"wanderlake.replace": "full"})  # all rows
    result = {
        "table": BRONZE_TABLE,
        "rows": batch.num_rows,
        "source": batch["_source_file"][0].as_py(),
    }
    log_event(log, "config_cities.loaded", **result)
    return result


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(load(dag_run_id="manual"))
