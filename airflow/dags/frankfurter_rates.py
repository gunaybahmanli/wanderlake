"""Daily ECB exchange rates from the Frankfurter API into the bronze layer."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import FRANKFURTER_RATES

RAW_BUCKET = "raw"

DOC_MD = """
### Frankfurter exchange rates (bronze)

Fetches the European Central Bank reference rates (base **EUR**) from the free
[Frankfurter API](https://frankfurter.dev) and stores the response **unchanged** in the
`raw` bucket:

`s3://raw/frankfurter/rates/date=YYYY-MM-DD/rates.json`

- **Source:** `GET https://api.frankfurter.dev/v2/providers/ecb/rates?date=<date>&base=EUR`
- **Schedule:** daily at 16:30 UTC, after the ECB publishes (around 16:00 CET).
- **Date:** the run's logical date (manual runs without one use the run time).
- **Idempotent:** re-running a date overwrites the same object.
- **Weekends and holidays:** the API returns the last business day. The file is still
  stored; the object metadata `rate_date` holds the date the rates are actually for.
- **Metadata:** `ingested_at`, `source_url`, `http_status`, `requested_date`, `rate_date`.

Then **load_bronze** reads that raw file and replaces the run date's rows in the Iceberg table
`iceberg.bronze.frankfurter_rates` (one row per quote currency, partitioned by month). Re-running
a date replaces its rows, so loads are idempotent. Bronze keeps weekend and holiday rows as
received (they repeat the last business day under their own `requested_date`); silver dedupes.

Code lives in the `ingestion` package (`ingestion.sources.frankfurter`).
"""


@dag(
    dag_id="frankfurter_rates",
    schedule="30 16 * * *",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=3,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "frankfurter"],
    doc_md=DOC_MD,
)
def frankfurter_rates():
    def run_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    @task
    def ingest() -> dict[str, str]:
        from ingestion.sources.frankfurter import ingest_rates

        return ingest_rates(run_date(), bucket=RAW_BUCKET)

    @task(outlets=[FRANKFURTER_RATES])
    def load_bronze() -> dict:
        from ingestion.sources.frankfurter import load_bronze as load

        run_id = get_current_context()["dag_run"].run_id
        return load(run_date(), dag_run_id=run_id, bucket=RAW_BUCKET)

    ingest() >> load_bronze()


frankfurter_rates()
