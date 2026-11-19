"""PyIceberg access to the shared Iceberg JDBC catalog (the same catalog Trino uses).

Configured only from environment variables:
    ICEBERG_CATALOG_NAME         catalog name; MUST equal Trino's iceberg.jdbc-catalog.catalog-name
                                 (both read this variable), or the two would see different tables
    ICEBERG_WAREHOUSE            e.g. s3://lakehouse/warehouse
    ICEBERG_CATALOG_DB_HOST      default postgres
    ICEBERG_CATALOG_DB_PORT      default 5432
    ICEBERG_CATALOG_DB_NAME      default iceberg_catalog
    ICEBERG_CATALOG_DB_USER, ICEBERG_CATALOG_DB_PASSWORD
    S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION   (generic S3 settings, path-style)
    ICEBERG_CATALOG_URI          optional full SQLAlchemy URI override (tests use SQLite)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThanOrEqual
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from ingestion.common.logging import get_logger, log_event

log = get_logger(__name__)


class IcebergConfigError(RuntimeError):
    """Raised when required catalog environment variables are missing."""


def _require(env: Mapping[str, str], *names: str) -> None:
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise IcebergConfigError(f"missing environment variables: {', '.join(missing)}")


def catalog_properties(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build SqlCatalog properties from the environment (no secrets are logged)."""
    env = os.environ if env is None else env
    _require(env, "ICEBERG_CATALOG_NAME", "ICEBERG_WAREHOUSE")

    uri = env.get("ICEBERG_CATALOG_URI")
    if not uri:
        _require(env, "ICEBERG_CATALOG_DB_USER", "ICEBERG_CATALOG_DB_PASSWORD")
        uri = "postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}".format(
            user=quote(env["ICEBERG_CATALOG_DB_USER"], safe=""),
            password=quote(env["ICEBERG_CATALOG_DB_PASSWORD"], safe=""),
            host=env.get("ICEBERG_CATALOG_DB_HOST") or "postgres",
            port=env.get("ICEBERG_CATALOG_DB_PORT") or "5432",
            db=env.get("ICEBERG_CATALOG_DB_NAME") or "iceberg_catalog",
        )

    props = {
        "uri": uri,
        "warehouse": env["ICEBERG_WAREHOUSE"],
        # The catalog tables (schema V1) are created by infra/compose/postgres/init; PyIceberg
        # must not change them. Tests on SQLite switch this on.
        "init_catalog_tables": env.get("ICEBERG_INIT_CATALOG_TABLES", "false"),
    }
    if env.get("S3_ENDPOINT"):
        props["s3.endpoint"] = env["S3_ENDPOINT"]
    if env.get("S3_ACCESS_KEY"):
        props["s3.access-key-id"] = env["S3_ACCESS_KEY"]
    if env.get("S3_SECRET_KEY"):
        props["s3.secret-access-key"] = env["S3_SECRET_KEY"]
    props["s3.region"] = env.get("S3_REGION") or "us-east-1"
    props["s3.force-virtual-addressing"] = "false"  # path-style (SeaweedFS, R2)
    return props


def get_catalog(env: Mapping[str, str] | None = None) -> SqlCatalog:
    """Return the shared catalog. Its name comes from ICEBERG_CATALOG_NAME (same as Trino)."""
    env = os.environ if env is None else env
    props = catalog_properties(env)
    return SqlCatalog(env["ICEBERG_CATALOG_NAME"], **props)


def ensure_table(
    identifier: str,
    schema: Schema,
    partition_spec: PartitionSpec,
    catalog: SqlCatalog | None = None,
    properties: Mapping[str, str] | None = None,
) -> Table:
    """Return the table, creating it if missing. The namespace must exist (make lakehouse-init)."""
    catalog = catalog or get_catalog()
    if catalog.table_exists(identifier):
        return catalog.load_table(identifier)
    table = catalog.create_table_if_not_exists(
        identifier,
        schema=schema,
        partition_spec=partition_spec,
        properties=dict(properties or {}),
    )
    log_event(log, "iceberg.table_created", table=identifier, location=table.location())
    return table


def replace_rows(table: Table, arrow_table: pa.Table, column: str, value: Any) -> int:
    """Atomically replace all rows where column == value with arrow_table (idempotent reload).

    Every row of arrow_table must have column == value, so a batch can only replace itself.
    Returns the number of rows written.
    """
    if column not in arrow_table.column_names:
        raise ValueError(f"column {column!r} is not in the data")
    if arrow_table.num_rows:
        # Compare in the column's own type (e.g. the string "2026-11-17" against a date column).
        expected = pc.cast(pa.scalar(value), arrow_table.schema.field(column).type)
        matches = pc.all(pc.equal(arrow_table[column], expected)).as_py()
        if not matches:
            raise ValueError(f"every row must have {column} == {value!r}")

    table.overwrite(
        arrow_table,
        overwrite_filter=EqualTo(column, value),
        snapshot_properties={"wanderlake.replace": f"{column}={value}"},
    )
    log_event(
        log,
        "iceberg.rows_replaced",
        table=".".join(table.name()),
        column=column,
        value=value,
        rows=arrow_table.num_rows,
    )
    return arrow_table.num_rows


def replace_range(table: Table, arrow_table: pa.Table, column: str, start: Any, end: Any) -> int:
    """Atomically replace all rows with start <= column <= end by arrow_table (idempotent reload
    of a date-range batch). Every row of arrow_table must lie in the range. Returns rows written.
    """
    if column not in arrow_table.column_names:
        raise ValueError(f"column {column!r} is not in the data")
    if arrow_table.num_rows:
        col_type = arrow_table.schema.field(column).type
        low = pc.cast(pa.scalar(start), col_type)
        high = pc.cast(pa.scalar(end), col_type)
        values = arrow_table[column]
        inside = pc.and_(pc.greater_equal(values, low), pc.less_equal(values, high))
        if not pc.all(inside).as_py():
            raise ValueError(f"every row must have {start!r} <= {column} <= {end!r}")

    table.overwrite(
        arrow_table,
        overwrite_filter=And(GreaterThanOrEqual(column, start), LessThanOrEqual(column, end)),
        snapshot_properties={"wanderlake.replace": f"{start}<={column}<={end}"},
    )
    log_event(
        log,
        "iceberg.range_replaced",
        table=".".join(table.name()),
        column=column,
        start=start,
        end=end,
        rows=arrow_table.num_rows,
    )
    return arrow_table.num_rows
