"""Supabase Postgres: connection and atomic replacement of a published table.

Connects through Supabase's session pooler (Supavisor, port 5432): session mode keeps one
server connection per client, which temporary tables and COPY need (transaction mode does not).
Settings come from the environment; the password is never logged:
    SUPABASE_DB_HOST, SUPABASE_DB_PORT (5432), SUPABASE_DB_USER (postgres.<project ref>),
    SUPABASE_DB_NAME (postgres), SUPABASE_DB_PASSWORD
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import psycopg
from psycopg import sql

from ingestion.common.logging import get_logger, log_event

log = get_logger(__name__)

SCHEMA = "api"


class SupabaseConfigError(RuntimeError):
    """Raised when required SUPABASE_DB_* environment variables are missing."""


class PublishError(RuntimeError):
    """Raised when a table cannot be published as expected."""


def connect(env: Mapping[str, str] | None = None) -> psycopg.Connection:
    env = os.environ if env is None else env
    required = ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise SupabaseConfigError(f"missing environment variables: {', '.join(missing)}")
    return psycopg.connect(
        host=env["SUPABASE_DB_HOST"],
        port=int(env.get("SUPABASE_DB_PORT") or 5432),
        user=env["SUPABASE_DB_USER"],
        password=env["SUPABASE_DB_PASSWORD"],
        dbname=env.get("SUPABASE_DB_NAME") or "postgres",
        sslmode="require",  # TLS to the pooler; the password never travels in clear text
        connect_timeout=15,
        application_name="wanderlake-publish",
        # Plain queries commit at once; each conn.transaction() block is then a real transaction
        # (with autocommit off, it would only be a savepoint inside an implicit outer one).
        autocommit=True,
    )


def table_columns(conn: Any, table: str) -> list[str]:
    """Columns of api.<table> in table order (empty if the table does not exist)."""
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = %s and table_name = %s order by ordinal_position",
            (SCHEMA, table),
        )
        return [row[0] for row in cur.fetchall()]


def check_columns(table: str, target: Sequence[str], source: Sequence[str]) -> None:
    """The gold table and api.<table> must have the same columns (any order)."""
    if not target:
        raise PublishError(f"{SCHEMA}.{table} does not exist: are the migrations applied?")
    missing, extra = set(target) - set(source), set(source) - set(target)
    if missing or extra:
        raise PublishError(
            f"columns differ between gold.{table} and {SCHEMA}.{table}: "
            f"missing in gold {sorted(missing)}, not in {SCHEMA} {sorted(extra)}; "
            "add a Supabase migration that matches the dbt contract"
        )


def replace_table(conn: Any, table: str, columns: Sequence[str], rows: Iterable[Sequence]) -> int:
    """Replace every row of api.<table> in one transaction.

    COPY the rows into a temporary staging table, then delete the old rows and insert the new
    ones. Readers see the old rows until the commit and all new rows after it, never a partial
    table. The table itself stays in place, so its RLS policies and grants never change.
    Returns the number of rows now in the table.
    """
    target = sql.Identifier(SCHEMA, table)
    staging = sql.Identifier(f"staging_{table}")
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    with conn.transaction(), conn.cursor() as cur:
        # One publisher per table at a time (a manual make publish next to the DAG).
        cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"wanderlake.publish.{table}",))
        cur.execute(
            sql.SQL("create temporary table {} (like {}) on commit drop").format(staging, target)
        )
        with cur.copy(sql.SQL("copy {} ({}) from stdin").format(staging, cols)) as copy:
            copied = 0
            for row in rows:
                copy.write_row(row)
                copied += 1
        cur.execute(sql.SQL("select count(*) from {}").format(staging))
        staged = cur.fetchone()[0]
        if staged != copied:
            raise PublishError(f"staging_{table}: copied {copied} rows but staged {staged}")
        cur.execute(sql.SQL("delete from {}").format(target))
        cur.execute(
            sql.SQL("insert into {} ({}) select {} from {}").format(target, cols, cols, staging)
        )
        inserted = cur.rowcount
    log_event(log, "supabase.table_replaced", table=f"{SCHEMA}.{table}", rows=inserted)
    return inserted


def count_rows(conn: Any, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(SCHEMA, table)))
        return cur.fetchone()[0]
