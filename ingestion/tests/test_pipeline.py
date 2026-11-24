from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.types import DateType, NestedField, StringType

from ingestion import pipeline, run
from ingestion.common.env import WanderlakeEnvError, raw_bucket, wanderlake_env
from ingestion.common.iceberg import ensure_table, replace_rows
from ingestion.pipeline import Context, Source

DAY = date(2026, 11, 24)  # a Tuesday


class Recorder:
    """Fake sources that record their calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def source(self, name: str, period: str = "daily", table: str | None = None, fail=0) -> Source:
        remaining = {"fail": fail}

        def run_step(day: date, ctx: Context) -> int:
            self.calls.append((name, day))
            if remaining["fail"]:
                remaining["fail"] -= 1
                raise ConnectionError(f"{name} broke")  # transient: retried
            return 10

        return Source(name, period, run_step, table)


def snapshot_table(catalog: SqlCatalog, name: str, days: list[date]) -> None:
    schema = Schema(
        NestedField(1, "snapshot_date", DateType(), required=True),
        NestedField(2, "value", StringType(), required=False),
    )
    table = ensure_table(name, schema, PartitionSpec(), catalog=catalog)
    for day in days:
        batch = pa.Table.from_pylist(
            [{"snapshot_date": day, "value": "x"}], schema=schema.as_arrow()
        )
        replace_rows(table, batch, "snapshot_date", day.isoformat())


# --- due logic ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "latest", "due"),
    [
        ("daily", DAY, True),
        ("weekly", None, True),
        ("weekly", date(2026, 11, 18), False),  # 6 days old
        ("weekly", date(2026, 11, 17), True),  # 7 days old
        ("monthly", None, True),
        ("monthly", date(2026, 11, 1), False),
        ("monthly", date(2026, 10, 31), True),
        ("monthly", date(2025, 11, 30), True),
    ],
)
def test_is_due(period: str, latest: date | None, due: bool) -> None:
    assert pipeline.is_due(period, latest, DAY) is due


def test_is_due_rejects_unknown_periods() -> None:
    with pytest.raises(ValueError):
        pipeline.is_due("hourly", None, DAY)


def test_latest_snapshot_reads_bronze(catalog: SqlCatalog) -> None:
    assert pipeline.latest_snapshot("bronze.missing", "snapshot_date", catalog) is None
    snapshot_table(catalog, "bronze.weekly_thing", [date(2026, 11, 10), date(2026, 11, 17)])
    assert pipeline.latest_snapshot("bronze.weekly_thing", "snapshot_date", catalog) == date(
        2026, 11, 17
    )


# --- daily runs --------------------------------------------------------------------------------


def test_order_matches_dependencies() -> None:
    names = list(pipeline.SOURCE_NAMES)
    assert names[0] == "config_cities"
    assert names.index("rest_countries") < names.index("fcdo_advice")
    assert len(set(names)) == len(names) == 9
    periods = {s.name: s.period for s in pipeline.SOURCES}
    assert periods["geonames_cities"] == "monthly"
    assert periods["rest_countries"] == periods["wikimedia_city_images"] == "weekly"
    assert all(s.table for s in pipeline.SOURCES if s.period != "daily")


def test_daily_runs_in_order_and_skips_periodic_sources_that_are_not_due(
    catalog: SqlCatalog,
) -> None:
    snapshot_table(catalog, "bronze.weekly_fresh", [date(2026, 11, 23)])
    snapshot_table(catalog, "bronze.weekly_stale", [date(2026, 11, 16)])
    rec = Recorder()
    sources = [
        rec.source("a"),
        rec.source("fresh", "weekly", "bronze.weekly_fresh"),
        rec.source("stale", "weekly", "bronze.weekly_stale"),
        rec.source("new", "monthly", "bronze.never_loaded"),
        rec.source("b"),
    ]
    ctx = Context(bucket="raw", run_id="test", catalog=catalog)

    report = pipeline.run_daily(DAY, ctx, sources=sources, retries=0)

    assert [c[0] for c in rec.calls] == ["a", "stale", "new", "b"]
    statuses = {r.source: r.status for r in report.results}
    assert statuses == {"a": "ran", "fresh": "skipped", "stale": "ran", "new": "ran", "b": "ran"}
    assert report.ok


def test_force_runs_everything(catalog: SqlCatalog) -> None:
    snapshot_table(catalog, "bronze.weekly_fresh", [DAY])
    rec = Recorder()
    sources = [rec.source("fresh", "weekly", "bronze.weekly_fresh")]
    ctx = Context(bucket="raw", run_id="t", catalog=catalog)
    pipeline.run_daily(DAY, ctx, sources=sources, force=True, retries=0)
    assert rec.calls == [("fresh", DAY)]


def test_a_failing_source_does_not_stop_the_others() -> None:
    rec = Recorder()
    sources = [rec.source("a", fail=5), rec.source("b")]
    report = pipeline.run_daily(DAY, Context("raw", "t"), sources=sources, retries=1, retry_delay=0)
    assert [c[0] for c in rec.calls] == ["a", "a", "b"]  # one retry, then move on
    assert not report.ok
    assert report.failed == ["a"]
    assert "a broke" in report.results[0].detail


def test_a_source_that_asks_to_come_back_later_is_deferred_not_failed() -> None:
    from ingestion.common.http import TryLaterError

    def busy(day: date, ctx: Context) -> int:
        raise TryLaterError("Wikidata is lagging")

    report = pipeline.run_daily(
        DAY, Context("raw", "t"), sources=[Source("w", "weekly", busy)], retries=1, retry_delay=0
    )
    assert report.ok
    assert report.results[0].status == "deferred"
    assert "deferred until the next run: w" in pipeline.markdown_summary(report)


def test_data_errors_are_not_retried() -> None:
    calls = []

    def bad_data(day: date, ctx: Context) -> int:
        calls.append(day)
        raise ValueError("expected one rate date")

    report = pipeline.run_daily(
        DAY, Context("raw", "t"), sources=[Source("a", "daily", bad_data)], retries=2, retry_delay=0
    )
    assert calls == [DAY]  # failed once, not three times
    assert report.failed == ["a"]


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (ConnectionError("reset"), True),
        (TimeoutError("slow"), True),
        (ValueError("bad data"), False),
        (RuntimeError("validation"), False),
    ],
)
def test_is_transient(exc: Exception, transient: bool) -> None:
    assert pipeline.is_transient(exc) is transient


def test_http_errors_are_transient_only_for_429_and_5xx() -> None:
    import requests

    def http_error(status: int) -> requests.HTTPError:
        response = requests.Response()
        response.status_code = status
        return requests.HTTPError(response=response)

    assert pipeline.is_transient(http_error(503))
    assert pipeline.is_transient(http_error(429))
    assert not pipeline.is_transient(http_error(404))


def test_a_retry_can_recover() -> None:
    rec = Recorder()
    report = pipeline.run_daily(
        DAY, Context("raw", "t"), sources=[rec.source("a", fail=1)], retries=1, retry_delay=0
    )
    assert report.ok and report.results[0].rows == 10


def test_only_limits_the_sources() -> None:
    rec = Recorder()
    sources = [rec.source("a"), rec.source("b")]
    pipeline.run_daily(DAY, Context("raw", "t"), sources=sources, only=["b"], retries=0)
    assert rec.calls == [("b", DAY)]


# --- backfill ------------------------------------------------------------------------------------


class FakeRates:
    """Stands in for ingestion.sources.frankfurter / cbar: records ingests and range loads."""

    def __init__(self, fail_days: set[date] | None = None) -> None:
        self.ingested: list[date] = []
        self.ranges: list[tuple[date, date]] = []
        self.fail_days = fail_days or set()

    def ingest_rates(self, day: date, bucket: str, s3=None) -> dict:
        if day in self.fail_days:
            raise RuntimeError("503 from the API")
        self.ingested.append(day)
        return {}

    def load_bronze_range(self, start, end, dag_run_id, bucket, catalog=None, s3=None) -> dict:
        self.ranges.append((start, end))
        return {"rows": 10 * len(self.ingested), "missing_raw": "none"}


@pytest.fixture
def fake_backfill(monkeypatch):
    from ingestion.sources import cbar, frankfurter, open_meteo

    fakes = {"frankfurter": FakeRates(), "cbar": FakeRates(), "history": []}
    for module, fake in ((frankfurter, fakes["frankfurter"]), (cbar, fakes["cbar"])):
        monkeypatch.setattr(module, "ingest_rates", fake.ingest_rates)
        monkeypatch.setattr(module, "load_bronze_range", fake.load_bronze_range)

    def fake_history(start, end, **kwargs):
        fakes["history"].append((start, end, kwargs["bucket"]))
        return [{"rows": 5}, {"rows": 7}]

    monkeypatch.setattr(open_meteo, "backfill_history", fake_history)
    return fakes


def test_backfill_ingests_every_day_and_loads_each_range_once(fake_backfill) -> None:
    rec = Recorder()
    sources = [rec.source("geonames_cities", "monthly", "bronze.anything")]
    report = pipeline.run_backfill(
        date(2026, 11, 20),
        DAY,
        Context("prod-raw", "t"),
        history_start=date(2023, 1, 1),
        pause_seconds=0,
        sources=sources,
        retries=0,
    )

    days = pipeline.date_range(date(2026, 11, 20), DAY)
    assert fake_backfill["frankfurter"].ingested == days
    assert fake_backfill["frankfurter"].ranges == [(days[0], DAY)]  # one commit for the range
    assert fake_backfill["cbar"].ranges == [(days[0], DAY)]
    assert fake_backfill["history"] == [(date(2023, 1, 1), date(2026, 11, 16), "prod-raw")]
    # Then a forced daily run for the end date: the monthly source runs although not due.
    assert rec.calls == [("geonames_cities", DAY)]
    by_source = {r.source: r for r in report.results}
    assert by_source["frankfurter_rates (backfill)"].rows == 50  # 5 days x 10 rows
    assert by_source["open_meteo_history (backfill)"].rows == 12
    assert report.ok


def test_backfill_reports_failed_days_and_still_loads_the_others(fake_backfill) -> None:
    fake_backfill["frankfurter"].fail_days = {date(2026, 11, 23)}
    report = pipeline.run_backfill(
        date(2026, 11, 23), DAY, Context("raw", "t"), pause_seconds=0, sources=[], retries=0
    )
    fx = report.results[0]
    assert fx.status == "failed" and "1 failed, first 2026-11-23" in fx.detail
    assert fx.rows == 10  # the other day still loaded, in the one range commit
    assert report.results[1].status == "ran"  # CBAR unaffected


def test_backfill_only_reruns_one_part_without_the_daily_run(fake_backfill) -> None:
    rec = Recorder()
    report = pipeline.run_backfill(
        date(2026, 7, 7),
        date(2026, 7, 9),
        Context("raw", "t"),
        pause_seconds=0,
        sources=[rec.source("config_cities")],
        retries=0,
        only=["cbar_rates"],
    )
    assert [r.source for r in report.results] == ["cbar_rates (backfill)"]
    assert fake_backfill["cbar"].ranges == [(date(2026, 7, 7), date(2026, 7, 9))]
    assert fake_backfill["frankfurter"].ingested == [] and fake_backfill["history"] == []
    assert rec.calls == []


def test_backfill_only_rejects_unknown_parts(fake_backfill) -> None:
    with pytest.raises(ValueError, match="unknown backfill parts"):
        pipeline.run_backfill(DAY, DAY, Context("raw", "t"), only=["geonames_cities"])


def test_date_range_rejects_reversed_ranges() -> None:
    with pytest.raises(ValueError):
        pipeline.date_range(DAY, date(2026, 11, 1))


# --- reporting and configuration -----------------------------------------------------------------


def test_markdown_summary_has_one_row_per_source() -> None:
    report = pipeline.RunReport("daily", DAY, "prod")
    report.results = [
        pipeline.StepResult("frankfurter_rates", "daily", "ran", 1234, seconds=1.5),
        pipeline.StepResult("geonames_cities", "monthly", "skipped", detail="latest 2026-11-01"),
        pipeline.StepResult("cbar_rates", "daily", "failed", detail="HTTPError: 503 | x"),
    ]
    text = pipeline.markdown_summary(report)
    assert "| frankfurter_rates | daily | ✅ ran | 1,234 | 1.5 |" in text
    assert "⏭️ skipped" in text and "503 \\| x" in text
    assert "**failed: cbar_rates**" in text


def test_run_id_uses_the_github_run() -> None:
    github = {"GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2"}
    assert pipeline.default_run_id(github) == "gha__42-2"
    assert pipeline.default_run_id({}).startswith("manual__")


def test_environment_configuration() -> None:
    assert wanderlake_env({}) == "dev"
    assert wanderlake_env({"WANDERLAKE_ENV": "PROD"}) == "prod"
    with pytest.raises(WanderlakeEnvError):
        wanderlake_env({"WANDERLAKE_ENV": "staging"})
    assert raw_bucket({}) == "raw"
    assert raw_bucket({"WANDERLAKE_RAW_BUCKET": "wanderlake-raw"}) == "wanderlake-raw"


def test_cli_writes_the_summary_and_fails_on_errors(monkeypatch, tmp_path: Path) -> None:
    rec = Recorder()
    monkeypatch.setattr(pipeline, "SOURCES", (rec.source("a"), rec.source("b", fail=9)))
    original = pipeline.run_daily

    def run_daily(day, ctx, **kwargs):
        return original(day, ctx, sources=pipeline.SOURCES, retries=0, **kwargs)

    monkeypatch.setattr(pipeline, "run_daily", run_daily)
    monkeypatch.setenv("WANDERLAKE_ENV", "prod")
    monkeypatch.setenv("WANDERLAKE_RAW_BUCKET", "wanderlake-raw")
    summary = tmp_path / "summary.md"

    code = run.main(["--summary", str(summary), "daily", "--date", "2026-11-24"])

    assert code == 1
    text = summary.read_text()
    assert "env prod" in text and "| a | daily | ✅ ran | 10 |" in text and "❌ failed" in text


def test_cli_backfill_only_is_parsed() -> None:
    args = run.build_parser().parse_args(
        ["backfill", "--start", "2026-07-07", "--end", "2026-09-12", "--only", "cbar_rates"]
    )
    assert args.only == ["cbar_rates"] and args.start == date(2026, 7, 7)
    with pytest.raises(SystemExit):
        run.build_parser().parse_args(["backfill", "--only", "geonames_cities"])
