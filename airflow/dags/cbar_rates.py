"""Daily official exchange rates of the Central Bank of Azerbaijan (CBAR) into bronze."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import CBAR_RATES

RAW_BUCKET = "raw"

DOC_MD = """
### CBAR exchange rates (raw XML to bronze)

Fetches the Central Bank of Azerbaijan's official rates (AZN per foreign unit) for the run date
and stores the XML **unchanged** in the `raw` bucket:

`s3://raw/cbar/rates/date=YYYY-MM-DD/rates.xml`

- **Source:** `GET https://www.cbar.az/currencies/DD.MM.YYYY.xml`
- **Schedule:** daily at 16:30 UTC, together with `frankfurter_rates` (a date's file exists by
  early afternoon UTC; CBAR does not document the time).
- **Date:** the run's logical date (manual runs without one use the run time). Future dates are
  refused: CBAR would answer them with today's file.
- **Weekends and holidays:** CBAR returns the last published file; `rate_date` holds its date.
- **Metadata:** `ingested_at`, `source_url`, `http_status`, `requested_date`, `rate_date`.

Then **load_bronze** parses the file (hardened parser, `defusedxml`) and replaces the run date's
rows in `iceberg.bronze.cbar_rates`: one row per foreign currency, with `nominal` (1 or 100) and
`rate_azn` (AZN for `nominal` units). Bank metals and SDR are skipped. Re-running a date replaces
its rows. Bronze keeps weekend and holiday rows as received; silver dedupes.

Code lives in the `ingestion` package (`ingestion.sources.cbar`).
"""


@dag(
    dag_id="cbar_rates",
    schedule="30 16 * * *",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "cbar"],
    doc_md=DOC_MD,
)
def cbar_rates():
    def run_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    @task
    def ingest() -> dict[str, str]:
        from ingestion.sources.cbar import ingest_rates

        return ingest_rates(run_date(), bucket=RAW_BUCKET)

    @task(outlets=[CBAR_RATES])
    def load_bronze() -> dict:
        from ingestion.sources.cbar import load_bronze as load

        run_id = get_current_context()["dag_run"].run_id
        return load(run_date(), dag_run_id=run_id, bucket=RAW_BUCKET)

    ingest() >> load_bronze()


cbar_rates()
