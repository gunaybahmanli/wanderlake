"""Daily Open-Meteo weather forecast and recent history for the configured cities into bronze."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task

RAW_BUCKET = "raw"

DOC_MD = """
### Open-Meteo weather (forecast and history)

For every city in `config/cities.yaml` (one request for all cities, each in its own timezone),
daily variables in metric units are fetched and stored **unchanged** in the `raw` bucket.

- **fetch_forecast >> load_forecast:** `api.open-meteo.com/v1/forecast`, 7 days ahead.
  Raw `open_meteo/forecast/run_date=YYYY-MM-DD/forecast.json`;
  bronze `iceberg.bronze.open_meteo_forecast`, replaced per run_date.
- **fetch_recent_history >> load_history:** `archive-api.open-meteo.com/v1/archive`,
  run_date-7 .. run_date-1. Raw `open_meteo/history/date=YYYY-MM-DD/history.json`;
  bronze `iceberg.bronze.open_meteo_history`, replaced per date range.

- **Schedule:** daily at 05:00 UTC. The run date is the logical date (manual runs: run time).
- **Why the last 7 days again:** the archive's newest days come from the ECMWF IFS model and are
  revised as the ERA5 reanalysis arrives (about 5 days later). Reloads are idempotent.
- **Validation (fails loudly):** one location per configured city, in order; each location's
  timezone matches the city's; the model grid point is within 30 km; every expected day present.
- **Backfill (one-time, not a DAG):** `make weather-backfill` loads 2023-01-01 until 8 days ago,
  month by month, pausing between requests.
- **Free API:** non-commercial use, < 10,000 calls/day; this DAG uses about 22 a day.

**License:** CC BY 4.0. Weather data by Open-Meteo.com (https://open-meteo.com/).

Code lives in the `ingestion` package (`ingestion.sources.open_meteo`).
"""


@dag(
    dag_id="open_meteo",
    schedule="0 5 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "weather", "open_meteo"],
    doc_md=DOC_MD,
)
def open_meteo():
    def run_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    def run_id() -> str:
        return get_current_context()["dag_run"].run_id

    @task
    def fetch_forecast() -> dict:
        from ingestion.sources.open_meteo import ingest_forecast

        return ingest_forecast(run_date(), bucket=RAW_BUCKET)

    @task
    def load_forecast() -> dict:
        from ingestion.sources.open_meteo import load_forecast as load

        return load(run_date(), dag_run_id=run_id(), bucket=RAW_BUCKET)

    @task
    def fetch_recent_history() -> dict:
        from ingestion.sources.open_meteo import ingest_recent_history

        return ingest_recent_history(run_date(), bucket=RAW_BUCKET)

    @task
    def load_history() -> dict:
        from ingestion.sources.open_meteo import load_recent_history

        return load_recent_history(run_date(), dag_run_id=run_id(), bucket=RAW_BUCKET)

    fetch_forecast() >> load_forecast()
    fetch_recent_history() >> load_history()


open_meteo()
