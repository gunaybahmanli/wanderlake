"""Monthly GeoNames cities15000 snapshot into bronze."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import GEONAMES_CITIES

RAW_BUCKET = "raw"

DOC_MD = """
### GeoNames cities (monthly reference snapshot)

Downloads the free GeoNames dump of cities with a population above 15,000 (or capitals) and
stores the zip **unchanged** in the `raw` bucket:

`s3://raw/geonames/cities15000/snapshot_date=YYYY-MM-DD/cities15000.zip`

- **Source:** `https://download.geonames.org/export/dump/cities15000.zip` (no account; about
  34,000 places; tab-separated, columns from the dump's readme.txt).
- **Schedule:** the 1st of every month, 04:00 UTC. The snapshot date is the run's logical date
  (manual runs without one use the run time). Re-running a date replaces its rows.
- **Metadata:** `ingested_at`, `source_url`, `http_status`, `snapshot_date`, `record_count`,
  `file_size`, `sha256`, `last_modified`.
- **Validation (fails loudly):** more than 20,000 rows, unique `geonameid`, coordinates in range,
  sha256 of the raw zip unchanged.
- **Cities check:** every `geonames_id` in `config/cities.yaml` must be in the snapshot, or
  load_bronze fails before writing anything.

**load_bronze** writes `iceberg.bronze.geonames_cities` (all 19 GeoNames columns, typed, empty
values as null, plus `snapshot_date` and the ingestion columns), partitioned by year.

**License:** CC BY 4.0. City data (c) GeoNames (https://www.geonames.org).

Code lives in the `ingestion` package (`ingestion.sources.geonames`).
"""


@dag(
    dag_id="geonames_cities",
    schedule="0 4 1 * *",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "reference", "geonames"],
    doc_md=DOC_MD,
)
def geonames_cities():
    def snapshot_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    @task
    def ingest() -> dict:
        from ingestion.sources.geonames import ingest_snapshot

        return ingest_snapshot(snapshot_date(), bucket=RAW_BUCKET)

    @task(outlets=[GEONAMES_CITIES])
    def load_bronze() -> dict:
        from ingestion.sources.geonames import load_bronze as load

        run_id = get_current_context()["dag_run"].run_id
        return load(snapshot_date(), dag_run_id=run_id, bucket=RAW_BUCKET)

    ingest() >> load_bronze()


geonames_cities()
