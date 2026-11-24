"""Run metadata of the production pipeline: ops.pipeline_runs and api.last_updated (ADR-0012).

    python -m ingestion.publish.runs start
    python -m ingestion.publish.runs finish --status success|failure|cancelled
        [--counts FILE] [--dbt-results FILE] [--summary FILE]

start applies the Supabase migrations and records the run as "running". finish records the final
status, the rows published per table (the JSON file python -m ingestion.publish --counts-json
writes), the first failed step (from STEPS_JSON, the workflow's toJSON(steps)) and dbt's failing
nodes (run_results.json), and appends a Markdown summary to --summary ($GITHUB_STEP_SUMMARY).

api.last_updated is not written here: the publish job moves it after verified row counts.
Both commands write to Supabase, so they follow the single-writer rule (job.should_write):
in dev they only print what they would record.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingestion.common.env import wanderlake_env
from ingestion.common.logging import get_logger, log_event

log = get_logger(__name__)

STATUSES = {"success": "succeeded", "failure": "failed", "cancelled": "cancelled"}
ERROR_SUMMARY_MAX = 2000


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    run_attempt: int
    environment: str
    trigger: str | None
    git_sha: str | None
    run_url: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunInfo:
        env = os.environ if env is None else env
        run_id = env.get("GITHUB_RUN_ID")
        url = None
        if run_id and env.get("GITHUB_REPOSITORY"):
            server = env.get("GITHUB_SERVER_URL") or "https://github.com"
            url = f"{server}/{env['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
        return cls(
            run_id=f"gha-{run_id}" if run_id else f"manual-{datetime.now(UTC):%Y%m%dT%H%M%SZ}",
            run_attempt=int(env.get("GITHUB_RUN_ATTEMPT") or 1),
            environment=wanderlake_env(env),
            trigger=env.get("GITHUB_EVENT_NAME") or "manual",
            git_sha=env.get("GITHUB_SHA"),
            run_url=url,
        )


# --- database ------------------------------------------------------------------------------------


def record_start(conn: Any, run: RunInfo) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "insert into ops.pipeline_runs "
            "(run_id, run_attempt, environment, trigger, status, git_sha, run_url) "
            "values (%s, %s, %s, %s, 'running', %s, %s) "
            "on conflict (run_id, run_attempt) do update set status = 'running', "
            "started_at = now(), finished_at = null, failed_step = null, error_summary = null",
            (run.run_id, run.run_attempt, run.environment, run.trigger, run.git_sha, run.run_url),
        )
    log_event(log, "runs.started", run_id=run.run_id, attempt=run.run_attempt)


def record_finish(
    conn: Any,
    run: RunInfo,
    status: str,
    rows_published: Mapping[str, int] | None,
    failed_step: str | None,
    error_summary: str | None,
) -> None:
    """Final status. Inserts the row if start never ran (for example a failed checkout)."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into ops.pipeline_runs (run_id, run_attempt, environment, trigger, status, "
            "git_sha, run_url, finished_at, rows_published, failed_step, error_summary) "
            "values (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s) "
            "on conflict (run_id, run_attempt) do update set status = excluded.status, "
            "finished_at = now(), rows_published = excluded.rows_published, "
            "failed_step = excluded.failed_step, error_summary = excluded.error_summary",
            (
                run.run_id,
                run.run_attempt,
                run.environment,
                run.trigger,
                status,
                run.git_sha,
                run.run_url,
                json.dumps(dict(rows_published)) if rows_published is not None else None,
                failed_step,
                error_summary,
            ),
        )
    log_event(log, "runs.finished", run_id=run.run_id, status=status, failed_step=failed_step)


def set_last_updated(conn: Any, published_at: datetime, run_id: str) -> None:
    """Move api.last_updated (one row). Call only after a verified publish."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into api.last_updated (id, published_at, run_id) values (true, %s, %s) "
            "on conflict (id) do update set published_at = excluded.published_at, "
            "run_id = excluded.run_id",
            (published_at, run_id),
        )
    log_event(log, "runs.last_updated", published_at=published_at.isoformat(), run_id=run_id)


# --- reading the job's results -------------------------------------------------------------------


def first_failed_step(steps_json: str | None) -> str | None:
    """The first step that failed the job, from the workflow's toJSON(steps). Uses conclusion,
    so a continue-on-error step (dbt source freshness) is not reported."""
    if not steps_json:
        return None
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError:
        return None
    for step_id, step in steps.items():
        if isinstance(step, dict) and step.get("conclusion") == "failure":
            return step_id
    return None


@dataclass(frozen=True)
class DbtResults:
    counts: dict[str, int]  # status -> number of nodes (pass, fail, warn, error, success, skipped)
    problems: list[tuple[str, str, str]]  # (status, node, message) for error, fail and warn

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [p for p in self.problems if p[0] in ("error", "fail")]


def read_dbt_results(path: Path | None) -> DbtResults | None:
    if path is None or not path.is_file():
        return None
    results = json.loads(path.read_text()).get("results", [])
    counts = Counter(r.get("status", "unknown") for r in results)
    problems = [
        (
            r["status"],
            r.get("unique_id", "?"),
            " ".join(str(r.get("message") or "").split())[:200],
        )
        for r in results
        if r.get("status") in ("error", "fail", "warn")
    ]
    return DbtResults(dict(counts), problems)


def read_counts(path: Path | None) -> dict[str, int] | None:
    """{table: rows} from the file python -m ingestion.publish --counts-json writes."""
    if path is None or not path.is_file():
        return None
    return {row["table"]: row["supabase"] for row in json.loads(path.read_text())}


def error_summary(failed_step: str | None, dbt: DbtResults | None) -> str | None:
    parts = []
    if failed_step:
        parts.append(f"failed step: {failed_step}")
    if dbt and dbt.failed:
        parts.append("dbt: " + "; ".join(f"{s} {node}: {msg}" for s, node, msg in dbt.failed))
    return " | ".join(parts)[:ERROR_SUMMARY_MAX] or None


def markdown(
    run: RunInfo,
    status: str,
    failed_step: str | None,
    dbt: DbtResults | None,
    rows: Mapping[str, int] | None,
) -> str:
    icon = {"succeeded": "✅", "failed": "❌", "cancelled": "⏹️"}.get(status, "")
    lines = [f"### Pipeline result: {icon} {status}", ""]
    if failed_step:
        lines.append(f"First failed step: `{failed_step}`. GitHub emails the failure.")
        lines.append("")
    if dbt is not None:
        order = ("success", "pass", "warn", "fail", "error", "skipped")
        counted = ", ".join(f"{dbt.counts[s]} {s}" for s in order if dbt.counts.get(s))
        lines.append(f"dbt build: {counted or 'no results'}.")
        if dbt.problems:
            lines += ["", "| dbt status | Node | Message |", "|---|---|---|"]
            for s, node, msg in dbt.problems:
                lines.append(f"| {s} | `{node}` | {msg.replace('|', chr(92) + '|')} |")
        lines.append("")
    if rows is not None:
        lines.append(f"Published {len(rows)} tables, {sum(rows.values()):,} rows in Supabase api.")
    elif status == "succeeded":
        lines.append("Nothing published.")
    lines.append(f"Run `{run.run_id}` attempt {run.run_attempt}, env {run.environment}.")
    return "\n".join(lines) + "\n"


# --- CLI -----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from ingestion.publish import job, migrations, supabase

    parser = argparse.ArgumentParser(prog="python -m ingestion.publish.runs", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="apply migrations, record the run as running")
    finish = sub.add_parser("finish", help="record the final status, write the summary")
    finish.add_argument("--status", required=True, choices=sorted(STATUSES))
    finish.add_argument("--counts", type=Path, help="publish counts JSON")
    finish.add_argument("--dbt-results", type=Path, help="dbt target/run_results.json")
    finish.add_argument("--summary", type=Path, help="append the Markdown summary to this file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)

    run = RunInfo.from_env()
    write = job.should_write()
    if args.command == "start":
        if not write:
            print(f"dry run (env {run.environment}): would record {run.run_id} as running")
            return 0
        with supabase.connect() as conn:
            migrations.apply(conn)
            record_start(conn, run)
        return 0

    status = STATUSES[args.status]
    failed_step = first_failed_step(os.environ.get("STEPS_JSON"))
    dbt = read_dbt_results(args.dbt_results)
    rows = read_counts(args.counts)
    text = markdown(run, status, failed_step, dbt, rows)
    print(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(text)
    if not write:
        print(f"dry run (env {run.environment}): would record {run.run_id} as {status}")
        return 0
    with supabase.connect() as conn:
        record_finish(conn, run, status, rows, failed_step, error_summary(failed_step, dbt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
