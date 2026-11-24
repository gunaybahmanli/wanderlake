"""Publish gold tables: read via PyIceberg, write to R2 (Parquet) and Supabase (api), verify.

Per table: read gold.<table> from the Iceberg catalog (the same tables Trino and dbt see; no
Trino needed), write the Parquet snapshot and latest files to R2, then replace api.<table> in
Supabase in one transaction. verify() then compares, for every table, the source row count
with Supabase count(*) and the row counts of both Parquet files read back from R2, and raises
if any differ.

Single writer (ADR-0012): only prod (WANDERLAKE_ENV=prod, the GitHub Actions pipeline) writes
to Supabase and R2. Elsewhere should_write() is False and callers do a dry run (dry_run_table:
read gold, report what would be written, write nothing), unless explicitly overridden with
--allow-dev-publish or WANDERLAKE_ALLOW_DEV_PUBLISH=1.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa

from ingestion.common.env import wanderlake_env
from ingestion.common.iceberg import get_catalog
from ingestion.common.logging import get_logger, log_event
from ingestion.publish import r2, supabase
from ingestion.publish.tables import PublishedTable

log = get_logger(__name__)


ALLOW_DEV_PUBLISH = "WANDERLAKE_ALLOW_DEV_PUBLISH"


class VerificationError(RuntimeError):
    """Raised when a published copy does not have the source's row count."""


def should_write(allow_dev: bool = False, env: Mapping[str, str] | None = None) -> bool:
    """True in prod. In dev only when explicitly overridden: the flag, or
    WANDERLAKE_ALLOW_DEV_PUBLISH=1."""
    env = os.environ if env is None else env
    if wanderlake_env(env) == "prod":
        return True
    return allow_dev or env.get(ALLOW_DEV_PUBLISH, "").strip() == "1"


@dataclass(frozen=True)
class DryRunResult:
    table: str
    snapshot_date: str
    source_rows: int
    targets: list[str]  # where a real publish would write


@dataclass(frozen=True)
class PublishResult:
    table: str
    snapshot_date: str  # ISO date (JSON-friendly: Airflow XCom)
    source_rows: int
    supabase_rows: int
    r2_keys: list[str]


@dataclass(frozen=True)
class TableCounts:
    table: str
    source: int
    supabase: int
    r2_snapshot: int
    r2_latest: int

    @property
    def ok(self) -> bool:
        return self.source == self.supabase == self.r2_snapshot == self.r2_latest


def today_utc() -> date:
    return datetime.now(UTC).date()


def read_gold(table: PublishedTable, snapshot_date: date, catalog: Any | None = None) -> pa.Table:
    """Read the gold table (filtered for tables that publish the last N days only).

    For an unfiltered table without delete files, the rows read must equal the Iceberg
    snapshot's total-records: a cross-check of the reader against the table metadata.
    """
    catalog = catalog or get_catalog()
    iceberg_table = catalog.load_table(table.source)
    arrow_table = iceberg_table.scan(row_filter=table.row_filter(snapshot_date)).to_arrow()
    snapshot = iceberg_table.current_snapshot()
    summary = snapshot.summary if snapshot is not None else None
    if table.cutoff(snapshot_date) is None and summary is not None:
        props = summary.additional_properties
        if props.get("total-delete-files", "0") == "0" and "total-records" in props:
            expected = int(props["total-records"])
            if arrow_table.num_rows != expected:
                raise VerificationError(
                    f"{table.source}: read {arrow_table.num_rows} rows, "
                    f"snapshot total-records is {expected}"
                )
    log_event(
        log,
        "gold.read",
        table=table.source,
        rows=arrow_table.num_rows,
        since=table.cutoff(snapshot_date) or "all",
    )
    return arrow_table


def rows_in_order(arrow_table: pa.Table, columns: list[str]) -> Iterable[tuple]:
    """Python rows in the given column order (lists stay lists: Postgres arrays)."""
    return zip(*(arrow_table.column(c).to_pylist() for c in columns), strict=True)


def publish_table(
    table: PublishedTable,
    snapshot_date: date,
    conn: Any,
    s3: Any,
    bucket_name: str,
    catalog: Any | None = None,
) -> PublishResult:
    arrow_table = read_gold(table, snapshot_date, catalog)
    columns = supabase.table_columns(conn, table.name)
    supabase.check_columns(table.name, columns, arrow_table.column_names)
    keys = r2.write_table(table.name, arrow_table, snapshot_date, s3, bucket_name)
    inserted = supabase.replace_table(
        conn, table.name, columns, rows_in_order(arrow_table, columns)
    )
    result = PublishResult(
        table=table.name,
        snapshot_date=snapshot_date.isoformat(),
        source_rows=arrow_table.num_rows,
        supabase_rows=inserted,
        r2_keys=keys,
    )
    log_event(log, "publish.table_done", **{k: v for k, v in asdict(result).items()})
    return result


def dry_run_table(
    table: PublishedTable, snapshot_date: date, bucket_name: str, catalog: Any | None = None
) -> DryRunResult:
    """Read gold and report what a publish would write. Connects to neither Supabase nor R2."""
    arrow_table = read_gold(table, snapshot_date, catalog)
    targets = [
        f"{supabase.SCHEMA}.{table.name}",
        f"s3://{bucket_name}/{r2.snapshot_key(table.name, snapshot_date)}",
        f"s3://{bucket_name}/{r2.latest_key(table.name)}",
    ]
    result = DryRunResult(table.name, snapshot_date.isoformat(), arrow_table.num_rows, targets)
    log_event(log, "publish.dry_run", table=table.name, rows=arrow_table.num_rows, targets=targets)
    return result


def format_dry_run(results: list[DryRunResult]) -> str:
    lines = ["DRY RUN: nothing was written (single writer: only WANDERLAKE_ENV=prod publishes)."]
    for r in results:
        lines.append(f"  {r.table}: {r.source_rows} rows -> {', '.join(r.targets)}")
    return "\n".join(lines)


def markdown_counts(counts: list[TableCounts], snapshot_date: date) -> str:
    lines = [
        f"### Published to Supabase api and R2 gold/ (snapshot {snapshot_date})",
        "",
        "| Table | Source | Supabase | R2 snapshot | R2 latest | OK |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for c in counts:
        lines.append(
            f"| {c.table} | {c.source:,} | {c.supabase:,} | {c.r2_snapshot:,} | "
            f"{c.r2_latest:,} | {'✅' if c.ok else '❌'} |"
        )
    return "\n".join(lines) + "\n"


def count_published(result: PublishResult, conn: Any, s3: Any, bucket_name: str) -> TableCounts:
    snapshot = date.fromisoformat(result.snapshot_date)
    return TableCounts(
        table=result.table,
        source=result.source_rows,
        supabase=supabase.count_rows(conn, result.table),
        r2_snapshot=r2.parquet_rows(r2.snapshot_key(result.table, snapshot), s3, bucket_name),
        r2_latest=r2.parquet_rows(r2.latest_key(result.table), s3, bucket_name),
    )


def format_counts(counts: list[TableCounts]) -> str:
    header = ("table", "source", "supabase", "r2_snapshot", "r2_latest", "ok")
    rows = [header] + [
        (c.table, c.source, c.supabase, c.r2_snapshot, c.r2_latest, "yes" if c.ok else "NO")
        for c in counts
    ]
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(header))]
    return "\n".join(
        "  ".join(
            str(v).ljust(w) if i == 0 else str(v).rjust(w)
            for i, (v, w) in enumerate(zip(r, widths, strict=True))
        )
        for r in rows
    )


def check_counts(counts: list[TableCounts], expected_tables: Iterable[str]) -> None:
    """Raise unless every expected table was counted and every count matches."""
    missing = sorted(set(expected_tables) - {c.table for c in counts})
    bad = [c for c in counts if not c.ok]
    if missing or bad:
        raise VerificationError(
            "publish verification failed"
            + (f"; tables not published: {missing}" if missing else "")
            + (f"; row counts differ for {[c.table for c in bad]}" if bad else "")
            + "\n"
            + format_counts(counts)
        )


def verify(
    results: list[PublishResult],
    conn: Any,
    s3: Any,
    bucket_name: str,
    expected_tables: Iterable[str],
) -> list[TableCounts]:
    counts = [count_published(r, conn, s3, bucket_name) for r in results]
    log.info("Published row counts:\n%s", format_counts(counts))
    check_counts(counts, expected_tables)
    return counts
