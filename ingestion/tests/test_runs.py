"""Run metadata (ingestion.publish.runs): ops.pipeline_runs rows, last_updated, job summary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ingestion.publish import job, runs, supabase

GHA_ENV = {
    "WANDERLAKE_ENV": "prod",
    "GITHUB_RUN_ID": "123",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_SHA": "abc123",
    "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_SERVER_URL": "https://github.com",
}


class Recorder:
    """A connection that records SQL and parameters."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> Recorder:
        return self

    def __enter__(self) -> Recorder:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def execute(self, query: str, params: Any = None) -> None:
        self.calls.append((query, params))


def test_run_info_from_github_actions() -> None:
    run = runs.RunInfo.from_env(GHA_ENV)
    assert (run.run_id, run.run_attempt, run.environment) == ("gha-123", 2, "prod")
    assert (run.trigger, run.git_sha) == ("schedule", "abc123")
    assert run.run_url == "https://github.com/owner/repo/actions/runs/123"


def test_run_info_outside_github_actions() -> None:
    run = runs.RunInfo.from_env({})
    assert run.run_id.startswith("manual-") and run.environment == "dev" and run.run_url is None


def test_start_and_finish_upsert_one_row_per_attempt() -> None:
    conn, run = Recorder(), runs.RunInfo.from_env(GHA_ENV)
    runs.record_start(conn, run)
    runs.record_finish(conn, run, "failed", {"dim_city": 10}, "dbt", "failed step: dbt")
    (start_sql, start_params), (finish_sql, finish_params) = conn.calls
    assert "'running'" in start_sql and "on conflict (run_id, run_attempt)" in start_sql
    assert start_params[:2] == ("gha-123", 2)
    assert "on conflict (run_id, run_attempt) do update" in finish_sql
    assert finish_params[4] == "failed"
    assert json.loads(finish_params[7]) == {"dim_city": 10}
    assert finish_params[8:] == ("dbt", "failed step: dbt")


def test_last_updated_is_a_single_row() -> None:
    conn = Recorder()
    when = datetime(2026, 11, 24, 17, 50, tzinfo=UTC)
    runs.set_last_updated(conn, when, "gha-123")
    sql, params = conn.calls[0]
    assert "api.last_updated" in sql and "on conflict (id)" in sql
    assert params == (when, "gha-123")


def test_first_failed_step_ignores_continue_on_error_steps() -> None:
    steps = {
        "record_start": {"outcome": "success", "conclusion": "success"},
        "ingest": {"outcome": "success", "conclusion": "success"},
        "freshness": {"outcome": "failure", "conclusion": "success"},  # continue-on-error
        "dbt": {"outcome": "failure", "conclusion": "failure"},
        "publish": {"outcome": "skipped", "conclusion": "skipped"},
    }
    assert runs.first_failed_step(json.dumps(steps)) == "dbt"
    assert runs.first_failed_step(json.dumps({"a": {"conclusion": "success"}})) is None
    assert runs.first_failed_step(None) is None
    assert runs.first_failed_step("not json") is None


def write_run_results(path: Path) -> Path:
    results = [
        {"unique_id": "model.wanderlake.dim_city", "status": "success", "message": "OK"},
        {"unique_id": "test.wanderlake.not_null_x", "status": "pass", "message": None},
        {"unique_id": "test.wanderlake.unique_y", "status": "fail", "message": "Got 2 results"},
        {"unique_id": "test.wanderlake.recent_z", "status": "warn", "message": "Got 1 | result"},
    ]
    path.write_text(json.dumps({"results": results}))
    return path


def test_dbt_results_count_statuses_and_list_problems(tmp_path: Path) -> None:
    dbt = runs.read_dbt_results(write_run_results(tmp_path / "run_results.json"))
    assert dbt is not None
    assert dbt.counts == {"success": 1, "pass": 1, "fail": 1, "warn": 1}
    assert [p[1] for p in dbt.failed] == ["test.wanderlake.unique_y"]
    assert runs.read_dbt_results(tmp_path / "missing.json") is None
    summary = runs.error_summary("dbt", dbt)
    assert summary == "failed step: dbt | dbt: fail test.wanderlake.unique_y: Got 2 results"


def test_markdown_summary(tmp_path: Path) -> None:
    run = runs.RunInfo.from_env(GHA_ENV)
    dbt = runs.read_dbt_results(write_run_results(tmp_path / "run_results.json"))
    text = runs.markdown(run, "failed", "dbt", dbt, None)
    assert "### Pipeline result: ❌ failed" in text and "`dbt`" in text
    assert "dbt build: 1 success, 1 pass, 1 warn, 1 fail." in text
    assert "Got 1 \\| result" in text  # pipes escaped for the Markdown table
    ok = runs.markdown(run, "succeeded", None, None, {"dim_city": 10, "dim_country": 250})
    assert "✅ succeeded" in ok and "Published 2 tables, 260 rows" in ok


def test_read_counts_uses_the_supabase_rows(tmp_path: Path) -> None:
    path = tmp_path / "counts.json"
    path.write_text(
        json.dumps(
            [
                {
                    "table": "dim_city",
                    "source": 10,
                    "supabase": 10,
                    "r2_snapshot": 10,
                    "r2_latest": 10,
                }
            ]
        )
    )
    assert runs.read_counts(path) == {"dim_city": 10}
    assert runs.read_counts(None) is None


def test_finish_in_dev_writes_the_summary_but_not_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in list(GHA_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WANDERLAKE_ENV", "dev")
    monkeypatch.delenv(job.ALLOW_DEV_PUBLISH, raising=False)
    monkeypatch.setattr(supabase, "connect", lambda *a, **k: pytest.fail("must not connect"))
    summary = tmp_path / "summary.md"

    assert runs.main(["finish", "--status", "success", "--summary", str(summary)]) == 0
    assert runs.main(["start"]) == 0
    assert "Pipeline result: ✅ succeeded" in summary.read_text()
