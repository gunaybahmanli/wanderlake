"""The ingestion pipeline as plain function calls: the production runner (python -m ingestion.run)
executes the same ingest and load functions the Airflow DAGs call, in dependency order.

- Daily sources run every day.
- Weekly and monthly sources run only when their latest bronze snapshot is older than their
  period (or with force=True). Every load replaces its own date's rows, so reruns are safe.
- A failing source does not stop the others; the caller decides the exit code from the results.
  Only temporary errors (network, HTTP 429/5xx, storage, catalog conflicts) are retried.

Dev (Airflow) keeps one DAG per source; prod (GitHub Actions) runs this module (ADR-0012).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow.compute as pc

from ingestion.common.http import SourceUnavailableError, TryLaterError
from ingestion.common.logging import get_logger, log_event

log = get_logger(__name__)

PERIODS = ("daily", "weekly", "monthly")
RETRY_DELAY_SECONDS = 60.0  # between attempts of one source (the DAGs wait 5 minutes)
FX_PAUSE_SECONDS = 1.0  # between days of a Frankfurter/CBAR backfill: one request a second
HISTORY_BACKFILL_START = date(2023, 1, 1)
FX_BACKFILL_DAYS = 400


@dataclass(frozen=True)
class Context:
    """What every step needs besides the date."""

    bucket: str
    run_id: str
    catalog: Any | None = None  # None: the catalog from the environment
    s3: Any | None = None  # None: the S3 client from the environment


@dataclass(frozen=True)
class Source:
    name: str
    period: str  # daily, weekly or monthly
    run: Callable[[date, Context], int]  # ingest + load for one date; returns rows loaded
    table: str | None = None  # bronze table whose latest snapshot decides if it is due
    column: str = "snapshot_date"


@dataclass
class StepResult:
    source: str
    period: str
    status: str  # ran, skipped, deferred (the source asked us to come back later) or failed
    rows: int = 0
    detail: str = ""
    seconds: float = 0.0


@dataclass
class RunReport:
    mode: str
    run_date: date
    environment: str
    results: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status != "failed" for r in self.results)

    @property
    def failed(self) -> list[str]:
        return [r.source for r in self.results if r.status == "failed"]


# --- the steps (same calls as the DAGs) -------------------------------------------------------


def _config_cities(day: date, ctx: Context) -> int:
    from ingestion.sources.config_cities import load

    return load(dag_run_id=ctx.run_id, catalog=ctx.catalog)["rows"]


def _snapshot_source(module: str) -> Callable[[date, Context], int]:
    def run(day: date, ctx: Context) -> int:
        import importlib

        mod = importlib.import_module(f"ingestion.sources.{module}")
        mod.ingest_snapshot(day, bucket=ctx.bucket, s3=ctx.s3)
        loaded = mod.load_bronze(
            day, dag_run_id=ctx.run_id, bucket=ctx.bucket, catalog=ctx.catalog, s3=ctx.s3
        )
        return loaded["rows"]

    return run


def _rates_source(module: str) -> Callable[[date, Context], int]:
    def run(day: date, ctx: Context) -> int:
        import importlib

        mod = importlib.import_module(f"ingestion.sources.{module}")
        mod.ingest_rates(day, bucket=ctx.bucket, s3=ctx.s3)
        loaded = mod.load_bronze(
            day, dag_run_id=ctx.run_id, bucket=ctx.bucket, catalog=ctx.catalog, s3=ctx.s3
        )
        return loaded["rows"]

    return run


def _open_meteo_forecast(day: date, ctx: Context) -> int:
    from ingestion.sources import open_meteo

    open_meteo.ingest_forecast(day, bucket=ctx.bucket, s3=ctx.s3)
    loaded = open_meteo.load_forecast(
        day, dag_run_id=ctx.run_id, bucket=ctx.bucket, catalog=ctx.catalog, s3=ctx.s3
    )
    return loaded["rows"]


def _open_meteo_history(day: date, ctx: Context) -> int:
    from ingestion.sources import open_meteo

    open_meteo.ingest_recent_history(day, bucket=ctx.bucket, s3=ctx.s3)
    loaded = open_meteo.load_recent_history(
        day, dag_run_id=ctx.run_id, bucket=ctx.bucket, catalog=ctx.catalog, s3=ctx.s3
    )
    return loaded["rows"]


# Dependency order: config first; rest_countries before fcdo (FCDO maps countries through
# bronze.rest_countries); geonames before the sources that are checked against it.
SOURCES: tuple[Source, ...] = (
    Source("config_cities", "daily", _config_cities),
    Source("geonames_cities", "monthly", _snapshot_source("geonames"), "bronze.geonames_cities"),
    Source("rest_countries", "weekly", _snapshot_source("rest_countries"), "bronze.rest_countries"),
    Source("fcdo_advice", "daily", _snapshot_source("fcdo")),
    Source(
        "wikimedia_city_images",
        "weekly",
        _snapshot_source("wikimedia"),
        "bronze.wikimedia_city_images",
    ),
    Source("frankfurter_rates", "daily", _rates_source("frankfurter")),
    Source("cbar_rates", "daily", _rates_source("cbar")),
    Source("open_meteo_forecast", "daily", _open_meteo_forecast),
    Source("open_meteo_history", "daily", _open_meteo_history),
)
SOURCE_NAMES = tuple(s.name for s in SOURCES)
# Parts of a backfill that can be rerun alone (run_backfill(only=...)), e.g. to fill a gap.
BACKFILL_PARTS = ("frankfurter_rates", "cbar_rates", "open_meteo_history")


# --- scheduling ----------------------------------------------------------------------------------


def is_due(period: str, latest: date | None, day: date) -> bool:
    """Daily: always. Weekly: the latest snapshot is 7 or more days old. Monthly: the latest
    snapshot is from an earlier calendar month. No snapshot yet: due."""
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}")
    if period == "daily" or latest is None:
        return True
    if period == "weekly":
        return (day - latest).days >= 7
    return (latest.year, latest.month) < (day.year, day.month)


def latest_snapshot(table: str, column: str, catalog: Any | None = None) -> date | None:
    """The newest value of column in a bronze table, or None if the table is missing or empty."""
    from ingestion.common.iceberg import get_catalog

    catalog = catalog or get_catalog()
    if not catalog.table_exists(table):
        return None
    values = catalog.load_table(table).scan(selected_fields=(column,)).to_arrow()[column]
    if len(values) == 0:
        return None
    return pc.max(values).as_py()


def default_run_id(env: dict[str, str] | None = None, now: datetime | None = None) -> str:
    """GitHub Actions: gha__<run id>-<attempt>; elsewhere: manual__<UTC time>."""
    env = dict(os.environ) if env is None else env
    if env.get("GITHUB_RUN_ID"):
        return f"gha__{env['GITHUB_RUN_ID']}-{env.get('GITHUB_RUN_ATTEMPT', '1')}"
    return f"manual__{(now or datetime.now(UTC)).strftime('%Y-%m-%dT%H:%M:%S')}"


def is_transient(exc: BaseException) -> bool:
    """Worth retrying: network, HTTP 429/5xx, storage and catalog connection or commit conflicts.
    Data and validation errors are not: they fail the same way every time."""
    import requests

    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is None or status == 429 or status >= 500
    if isinstance(
        exc,
        (
            requests.ConnectionError,
            requests.Timeout,
            ConnectionError,
            TimeoutError,
            SourceUnavailableError,
        ),
    ):
        return True
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    return module.startswith(("botocore", "urllib3")) or name in {
        "CommitFailedException",
        "OperationalError",
        "InterfaceError",
    }


def _attempt(
    source: Source, day: date, ctx: Context, retries: int, retry_delay: float
) -> StepResult:
    started = time.monotonic()
    for attempt in range(retries + 1):
        try:
            rows = source.run(day, ctx)
            seconds = round(time.monotonic() - started, 1)
            log_event(log, "pipeline.step_done", source=source.name, date=day, rows=rows)
            return StepResult(source.name, source.period, "ran", rows, seconds=seconds)
        except TryLaterError as exc:
            # Polite back-off already happened inside the source; it stays due for the next run.
            log_event(log, "pipeline.step_deferred", source=source.name, date=day, reason=exc)
            seconds = round(time.monotonic() - started, 1)
            return StepResult(source.name, source.period, "deferred", 0, str(exc)[:300], seconds)
        except Exception as exc:  # noqa: BLE001 (isolate sources: report, then continue)
            log_event(
                log,
                "pipeline.step_failed",
                source=source.name,
                date=day,
                attempt=attempt + 1,
                error=f"{type(exc).__name__}: {exc}",
            )
            last = exc
            if not is_transient(exc):
                break  # a data or validation error: retrying would fail the same way
            if attempt < retries and retry_delay:
                time.sleep(retry_delay)
    seconds = round(time.monotonic() - started, 1)
    detail = f"{type(last).__name__}: {last}"[:300]
    return StepResult(source.name, source.period, "failed", detail=detail, seconds=seconds)


def run_daily(
    day: date,
    ctx: Context,
    *,
    environment: str = "dev",
    force: bool = False,
    only: Iterable[str] | None = None,
    sources: Sequence[Source] = SOURCES,
    retries: int = 2,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> RunReport:
    """Run every daily source, and the weekly/monthly ones that are due (or all with force)."""
    wanted = set(only) if only else None
    report = RunReport("daily", day, environment)
    for source in sources:
        if wanted is not None and source.name not in wanted:
            continue
        if not force and source.table and source.period != "daily":
            latest = latest_snapshot(source.table, source.column, catalog=ctx.catalog)
            if not is_due(source.period, latest, day):
                report.results.append(
                    StepResult(source.name, source.period, "skipped", detail=f"latest {latest}")
                )
                log_event(log, "pipeline.step_skipped", source=source.name, latest=latest)
                continue
        report.results.append(_attempt(source, day, ctx, retries, retry_delay))
    return report


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _backfill_rates(
    name: str,
    module: str,
    days: list[date],
    ctx: Context,
    pause_seconds: float,
    retries: int,
    retry_delay: float,
) -> StepResult:
    """Ingest each day's raw file (retrying failed days), then load the range in one commit."""
    import importlib

    mod = importlib.import_module(f"ingestion.sources.{module}")
    started = time.monotonic()
    failed: list[str] = []
    for i, day in enumerate(days):
        result = _attempt(
            Source(name, "daily", lambda d, c: _ingest_rates(mod, d, c)),
            day,
            ctx,
            retries,
            retry_delay,
        )
        if result.status != "ran":
            failed.append(f"{day}: {result.detail}")
        if pause_seconds and i < len(days) - 1:
            time.sleep(pause_seconds)
    try:
        loaded = mod.load_bronze_range(
            days[0], days[-1], ctx.run_id, bucket=ctx.bucket, catalog=ctx.catalog, s3=ctx.s3
        )
        rows, missing = loaded["rows"], loaded["missing_raw"]
    except Exception as exc:  # noqa: BLE001 (report, then continue)
        rows, missing = 0, "load failed"
        failed.append(f"load: {type(exc).__name__}: {exc}")
    detail = f"{len(days)} days {days[0]}..{days[-1]}"
    if failed:
        detail += f"; {len(failed)} failed, first {failed[0]}"
    if missing not in ("none", "load failed"):
        detail += f"; no raw file: {missing}"
    return StepResult(
        f"{name} (backfill)",
        "daily",
        "failed" if failed else "ran",
        rows,
        detail[:300],
        round(time.monotonic() - started, 1),
    )


def _ingest_rates(mod: Any, day: date, ctx: Context) -> int:
    mod.ingest_rates(day, bucket=ctx.bucket, s3=ctx.s3)
    return 0


def _backfill_history(
    history_start: date, end: date, ctx: Context, history_pause_seconds: float | None
) -> StepResult:
    """Open-Meteo history month by month, history_start..end-8 (the daily run covers the rest)."""
    from ingestion.sources import open_meteo

    history_end = end - timedelta(days=open_meteo.HISTORY_WINDOW_DAYS + 1)
    started = time.monotonic()
    try:
        kwargs = {} if history_pause_seconds is None else {"pause_seconds": history_pause_seconds}
        loaded = open_meteo.backfill_history(
            history_start, history_end, bucket=ctx.bucket, catalog=ctx.catalog, s3=ctx.s3, **kwargs
        )
        rows = sum(r["rows"] for r in loaded)
        detail = f"{len(loaded)} months {history_start}..{history_end}"
        status = "ran"
    except Exception as exc:  # noqa: BLE001 (report, then continue with the daily run)
        rows, status, detail = 0, "failed", f"{type(exc).__name__}: {exc}"[:300]
    return StepResult(
        "open_meteo_history (backfill)",
        "daily",
        status,
        rows,
        detail,
        round(time.monotonic() - started, 1),
    )


def run_backfill(
    start: date,
    end: date,
    ctx: Context,
    *,
    environment: str = "dev",
    history_start: date = HISTORY_BACKFILL_START,
    pause_seconds: float = FX_PAUSE_SECONDS,
    history_pause_seconds: float | None = None,
    sources: Sequence[Source] = SOURCES,
    retries: int = 2,
    retry_delay: float = RETRY_DELAY_SECONDS,
    only: Iterable[str] | None = None,
) -> RunReport:
    """One-time backfill: Frankfurter and CBAR raw files day by day (start..end) loaded in one
    commit per source, Open-Meteo history month by month (history_start..end-8), then a forced
    daily run for end (current reference snapshots). Idempotent: every range replaces its rows.

    only: run just these BACKFILL_PARTS (no daily run), e.g. to refill one source's gap."""
    parts = set(BACKFILL_PARTS if only is None else only)
    unknown = parts - set(BACKFILL_PARTS)
    if unknown:
        raise ValueError(f"unknown backfill parts {sorted(unknown)}; choose from {BACKFILL_PARTS}")

    report = RunReport("backfill", end, environment)
    days = date_range(start, end)
    # Frankfurter and CBAR: raw files day by day (one request a second), then one Iceberg commit
    # per source for the whole range (load_bronze_range).
    for name, module in (("frankfurter_rates", "frankfurter"), ("cbar_rates", "cbar")):
        if name not in parts:
            continue
        report.results.append(
            _backfill_rates(name, module, days, ctx, pause_seconds, retries, retry_delay)
        )

    if "open_meteo_history" in parts:
        report.results.append(_backfill_history(history_start, end, ctx, history_pause_seconds))
    if only is not None:
        return report

    daily = run_daily(
        end,
        ctx,
        environment=environment,
        force=True,
        sources=sources,
        retries=retries,
        retry_delay=retry_delay,
    )
    report.results.extend(daily.results)
    return report


# --- reporting -----------------------------------------------------------------------------------

_ICONS = {
    "ran": "✅ ran",
    "skipped": "⏭️ skipped",
    "deferred": "⏳ deferred",
    "failed": "❌ failed",
}


def markdown_summary(report: RunReport) -> str:
    """A GitHub job summary: one row per source with the rows loaded."""
    lines = [
        f"### Ingestion ({report.mode}, {report.run_date}, env {report.environment})",
        "",
        "| Source | Period | Status | Rows loaded | Seconds | Detail |",
        "|---|---|---|---:|---:|---|",
    ]
    for r in report.results:
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r.source} | {r.period} | {_ICONS[r.status]} | {r.rows:,} | {r.seconds:g} "
            f"| {detail} |"
        )
    total = sum(r.rows for r in report.results)
    verdict = "all sources OK" if report.ok else f"**failed: {', '.join(report.failed)}**"
    deferred = [r.source for r in report.results if r.status == "deferred"]
    if deferred:
        verdict += f"; deferred until the next run: {', '.join(deferred)}"
    lines += ["", f"Total rows loaded: {total:,}. Result: {verdict}.", ""]
    return "\n".join(lines)
