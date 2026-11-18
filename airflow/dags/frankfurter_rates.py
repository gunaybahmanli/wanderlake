"""Daily ECB exchange rates from the Frankfurter API into the bronze layer."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task

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
    @task
    def ingest() -> dict[str, str]:
        from ingestion.sources.frankfurter import ingest_rates

        context = get_current_context()
        run_date = context.get("logical_date") or context["dag_run"].run_after
        return ingest_rates(run_date.date(), bucket=RAW_BUCKET)

    ingest()


frankfurter_rates()
