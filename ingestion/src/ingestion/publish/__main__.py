"""python -m ingestion.publish [--date YYYY-MM-DD] [--table NAME ...]

Applies the Supabase migrations, publishes the gold tables to R2 and Supabase, and verifies the
row counts (exit code 1 on any mismatch). Same code as the publish_cloud DAG.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from ingestion.publish import job, migrations, r2, supabase
from ingestion.publish.tables import TABLE_NAMES, get_table


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ingestion.publish", description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="snapshot date")
    parser.add_argument("--table", action="append", choices=TABLE_NAMES, help="repeatable")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    snapshot_date = args.date or job.today_utc()
    names = args.table or list(TABLE_NAMES)
    s3, bucket_name = r2.client(), r2.bucket()
    with supabase.connect() as conn:
        migrations.apply(conn)
        results = [
            job.publish_table(get_table(n), snapshot_date, conn, s3, bucket_name) for n in names
        ]
        counts = job.verify(results, conn, s3, bucket_name, expected_tables=names)
    print(f"\nPublished {len(counts)} tables (snapshot_date={snapshot_date}):")
    print(job.format_counts(counts))


if __name__ == "__main__":
    main()
