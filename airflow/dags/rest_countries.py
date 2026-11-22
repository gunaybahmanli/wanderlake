"""Weekly REST Countries reference snapshot into bronze."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import REST_COUNTRIES

RAW_BUCKET = "raw"

DOC_MD = """
### REST Countries reference data (weekly snapshot)

Fetches the full country list from **REST Countries v5** (API key in `REST_COUNTRIES_API_KEY`;
v1-v4 are deprecated) and stores every page **unchanged** in the `raw` bucket:

`s3://raw/rest_countries/all/snapshot_date=YYYY-MM-DD/page=N.json`

- **Source:** `GET https://api.restcountries.com/countries/v5?limit=100&offset=N&response_fields=...`
  (3 pages of at most 100 records; about 254 entities). Free plan: 1,000 requests a month.
- **Schedule:** Mondays 03:00 UTC. The snapshot date is the run's logical date (manual runs
  without one use the run time). Re-running a date replaces its raw pages and bronze rows.
- **Validation (fails loudly):** a v5 country list (not v3.1's deprecation answer), all pages
  present, at least 200 countries with an ISO code, no malformed or duplicate codes. Entities
  without an ISO code (e.g. Abkhazia) stay in raw and are left out of bronze.
- **Cities check:** every `country_code` in `config/cities.yaml` must be in the snapshot, or
  load_bronze fails before writing anything.

**load_bronze** writes `iceberg.bronze.rest_countries`: one row per country and snapshot date.
Nested values (capitals, languages, currencies, timezones) stay JSON strings in bronze; silver
flattens them.

Code lives in the `ingestion` package (`ingestion.sources.rest_countries`).
"""


@dag(
    dag_id="rest_countries",
    schedule="0 3 * * 1",
    start_date=datetime(2026, 10, 5),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "reference", "rest_countries"],
    doc_md=DOC_MD,
)
def rest_countries():
    def snapshot_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    @task
    def ingest() -> dict:
        from ingestion.sources.rest_countries import ingest_snapshot

        return ingest_snapshot(snapshot_date(), bucket=RAW_BUCKET)

    @task(outlets=[REST_COUNTRIES])
    def load_bronze() -> dict:
        from ingestion.sources.rest_countries import load_bronze as load

        run_id = get_current_context()["dag_run"].run_id
        return load(snapshot_date(), dag_run_id=run_id, bucket=RAW_BUCKET)

    ingest() >> load_bronze()


rest_countries()
