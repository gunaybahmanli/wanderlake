"""python -m ingestion.publish [--date YYYY-MM-DD] [--table NAME ...] [--allow-dev-publish]
                             [--counts-json FILE] [--summary FILE]

Applies the Supabase migrations, publishes the gold tables to R2 and Supabase, verifies the row
counts (exit code 1 on any mismatch), then moves api.last_updated. Same code as the
publish_cloud DAG.

Single writer (ADR-0012): only WANDERLAKE_ENV=prod writes. In dev this is a dry run that reads
gold and prints what would be written, unless --allow-dev-publish (or
WANDERLAKE_ALLOW_DEV_PUBLISH=1) overrides it.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from ingestion.common.env import wanderlake_env
from ingestion.publish import job, migrations, r2, runs, supabase
from ingestion.publish.tables import TABLE_NAMES, get_table

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ingestion.publish", description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="snapshot date")
    parser.add_argument("--table", action="append", choices=TABLE_NAMES, help="repeatable")
    parser.add_argument(
        "--allow-dev-publish",
        action="store_true",
        help="write from dev anyway (overrides the single-writer rule; avoid)",
    )
    parser.add_argument("--counts-json", type=Path, help="write the verified counts here")
    parser.add_argument("--summary", type=Path, help="append a Markdown table to this file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    snapshot_date = args.date or job.today_utc()
    names = args.table or list(TABLE_NAMES)
    bucket_name = r2.bucket()

    if not job.should_write(args.allow_dev_publish):
        results = [job.dry_run_table(get_table(n), snapshot_date, bucket_name) for n in names]
        print(f"\n{job.format_dry_run(results)}")
        return

    environment = wanderlake_env()
    if environment != "prod":
        log.warning("Publishing from %s: single-writer rule overridden", environment)
    s3 = r2.client()
    with supabase.connect() as conn:
        migrations.apply(conn)
        results = [
            job.publish_table(get_table(n), snapshot_date, conn, s3, bucket_name) for n in names
        ]
        counts = job.verify(results, conn, s3, bucket_name, expected_tables=names)
        # Only after every count matched, and only for a full publish: the site's "last updated".
        if set(names) == set(TABLE_NAMES):
            runs.set_last_updated(conn, datetime.now(UTC), runs.RunInfo.from_env().run_id)
    print(f"\nPublished {len(counts)} tables (snapshot_date={snapshot_date}):")
    print(job.format_counts(counts))
    if args.counts_json:
        args.counts_json.write_text(json.dumps([asdict(c) for c in counts], indent=2))
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(job.markdown_counts(counts, snapshot_date))


if __name__ == "__main__":
    main()
