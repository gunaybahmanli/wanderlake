"""Publish gold to the cloud: Supabase (schema api, read-only REST) and R2 Parquet snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import GOLD, SUPABASE_API

from ingestion.publish.tables import TABLE_NAMES

DOC_MD = """
### publish_cloud: gold to Supabase and R2

Copies the gold tables the website needs from the lakehouse to the cloud:

- **Supabase** (Postgres, schema `api`): served to the website by Supabase's REST API with the
  anon key, read-only (row-level security with a SELECT-only policy; ADR-0010).
- **Cloudflare R2** (bucket `wanderlake-lake`): one Parquet file per table and day,
  `gold/<table>/snapshot_date=YYYY-MM-DD/data.parquet`, plus `gold/<table>/latest/data.parquet`.

Tables: `dim_city`, `dim_country`, `city_overview`, `fct_exchange_rate` (last 400 days),
`fct_weather_daily`, `agg_city_climate_monthly`, `weather_forecast_latest`,
`travel_advice_current`, `city_images`; then `api.last_updated` (the site's "last
updated" time).

**Single writer (ADR-0012): on the laptop this DAG is a dry run.** Production is published only
by the GitHub Actions pipeline (`WANDERLAKE_ENV=prod`). Here (`WANDERLAKE_ENV=dev`) the tasks
read gold and log what would be written, migrations are not applied, and `verify_counts` is
skipped, so no `supabase.api` asset event is recorded. To write from the laptop anyway (avoid:
the nightly prod run overwrites it), set `WANDERLAKE_ALLOW_DEV_PUBLISH=1`.

**Schedule:** no clock time. It runs when the `gold` asset is updated, which is the last task of
`wanderlake_transform`, so every successful transform run is published once.

**Tasks:**
1. `apply_migrations`: applies new SQL files from `supabase/migrations/` (tables, RLS, grants).
2. `publish_table` (one mapped task per table): reads `gold.<table>` with PyIceberg, writes both
   Parquet files to R2, then replaces `api.<table>` in **one transaction** (COPY into a temporary
   staging table, delete and insert), so readers never see a partial table.
3. `verify_counts`: source rows vs Supabase `count(*)` vs both Parquet files read back from R2,
   for every table; fails on any difference. Success moves `api.last_updated` and records an
   event on `supabase.api`.

The snapshot date is the UTC date of the run. Reads use PyIceberg, so the job itself needs no
Trino; it follows `wanderlake_transform`, which needs `make mode-full`. Manual run of the same
code: `make publish` (also a dry run in dev). Credentials: `CLOUD_S3_*` and `SUPABASE_DB_*`
from `.env` (`docs/runbooks/cloud-setup.md`).
"""


def snapshot_date_iso() -> str:
    """UTC date the run was scheduled for (the same for every task of a run)."""
    return get_current_context()["dag_run"].run_after.date().isoformat()


@dag(
    dag_id="publish_cloud",
    schedule=[GOLD],
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["publish", "cloud"],
    doc_md=DOC_MD,
)
def publish_cloud():
    @task
    def apply_migrations() -> list[str]:
        from ingestion.publish import job, migrations, supabase

        if not job.should_write():
            print("Dry run (single writer: only WANDERLAKE_ENV=prod publishes): no migrations.")
            return []
        with supabase.connect() as conn:
            return migrations.apply(conn)

    @task(map_index_template="{{ task.op_kwargs['table'] }}")
    def publish_table(table: str) -> dict:
        from dataclasses import asdict
        from datetime import date

        from ingestion.publish import job, r2, supabase
        from ingestion.publish.tables import get_table

        snapshot = date.fromisoformat(snapshot_date_iso())
        if not job.should_write():
            return asdict(job.dry_run_table(get_table(table), snapshot, r2.bucket()))
        with supabase.connect() as conn:
            result = job.publish_table(
                get_table(table),
                snapshot,
                conn,
                r2.client(),
                r2.bucket(),
            )
        return asdict(result)

    @task(outlets=[SUPABASE_API])
    def verify_counts(results: list[dict]) -> list[dict]:
        from dataclasses import asdict
        from datetime import UTC
        from datetime import datetime as dt

        from airflow.exceptions import AirflowSkipException

        from ingestion.publish import job, r2, runs, supabase

        if not job.should_write():
            dry = [job.DryRunResult(**r) for r in results]
            print(job.format_dry_run(dry))
            # Skipped, not succeeded: no supabase.api asset event for a dry run.
            raise AirflowSkipException("dry run: nothing was published (WANDERLAKE_ENV=dev)")
        published = [job.PublishResult(**r) for r in results]
        with supabase.connect() as conn:
            counts = job.verify(published, conn, r2.client(), r2.bucket(), TABLE_NAMES)
            run_id = get_current_context()["dag_run"].run_id
            runs.set_last_updated(conn, dt.now(UTC), run_id)
        return [asdict(c) for c in counts]

    migrated = apply_migrations()
    published = publish_table.expand(table=list(TABLE_NAMES))
    migrated >> published
    verify_counts(published)


publish_cloud()
