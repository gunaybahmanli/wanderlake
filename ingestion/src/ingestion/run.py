"""python -m ingestion.run daily|backfill: the ingestion pipeline without Airflow (ADR-0012).

    python -m ingestion.run daily [--date YYYY-MM-DD] [--force] [--only SOURCE ...]
    python -m ingestion.run backfill [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                     [--history-start YYYY-MM-DD] [--only PART ...]

Calls the same ingest and load functions as the Airflow DAGs. Configuration comes only from the
environment (WANDERLAKE_ENV, WANDERLAKE_RAW_BUCKET, S3_*, ICEBERG_*). --summary FILE appends a
Markdown table (rows loaded per source) to FILE, e.g. $GITHUB_STEP_SUMMARY. Exit code 1 if any
source failed; the other sources still run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ingestion import pipeline
from ingestion.common.env import raw_bucket, wanderlake_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingestion.run", description=__doc__)
    parser.add_argument("--summary", type=Path, help="append a Markdown summary to this file")
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="daily sources, plus weekly/monthly ones that are due")
    daily.add_argument("--date", type=date.fromisoformat, help="default: today (UTC)")
    daily.add_argument("--force", action="store_true", help="run weekly/monthly sources anyway")
    daily.add_argument("--only", action="append", choices=pipeline.SOURCE_NAMES, metavar="SOURCE")

    backfill = sub.add_parser("backfill", help="one-time history load (idempotent)")
    backfill.add_argument("--end", type=date.fromisoformat, help="default: today (UTC)")
    backfill.add_argument(
        "--start",
        type=date.fromisoformat,
        help=f"Frankfurter/CBAR start; default: {pipeline.FX_BACKFILL_DAYS} days up to --end",
    )
    backfill.add_argument(
        "--history-start",
        type=date.fromisoformat,
        default=pipeline.HISTORY_BACKFILL_START,
        help="Open-Meteo history start (default 2023-01-01)",
    )
    backfill.add_argument(
        "--only",
        action="append",
        choices=pipeline.BACKFILL_PARTS,
        metavar="PART",
        help="rerun just this part (repeatable; no daily run), e.g. to refill a gap",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)

    environment = wanderlake_env()
    ctx = pipeline.Context(bucket=raw_bucket(), run_id=pipeline.default_run_id())
    today = datetime.now(UTC).date()
    print(f"ingestion run: env={environment} bucket={ctx.bucket} run_id={ctx.run_id}")

    if args.command == "daily":
        report = pipeline.run_daily(
            args.date or today, ctx, environment=environment, force=args.force, only=args.only
        )
    else:
        end = args.end or today
        start = args.start or end - timedelta(days=pipeline.FX_BACKFILL_DAYS - 1)
        report = pipeline.run_backfill(
            start,
            end,
            ctx,
            environment=environment,
            history_start=args.history_start,
            only=args.only,
        )

    summary = pipeline.markdown_summary(report)
    print(summary)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
