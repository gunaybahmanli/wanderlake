"""Daily UK FCDO foreign travel advice for the countries of our cities into bronze."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import FCDO_ADVICE

RAW_BUCKET = "raw"

DOC_MD = """
### FCDO travel advice (GOV.UK Content API)

Fetches the UK Foreign, Commonwealth & Development Office travel advice for every country in
`config/cities.yaml` and stores each response **unchanged** in the `raw` bucket:

`s3://raw/fcdo/advice/snapshot_date=YYYY-MM-DD/<country_code>.json` (plus `_index.json`)

- **Source:** `https://www.gov.uk/api/content/foreign-travel-advice` (index) and
  `.../foreign-travel-advice/<slug>` per country. No key; at most 10 requests per second (this
  DAG makes 11 requests a day, 1 second apart).
- **Mapping (no hand-typed list):** ISO code -> names in the latest `bronze.rest_countries`
  snapshot -> FCDO country name or synonym in the index. Fails if a country has no match or
  more than one.
- **Schedule:** daily at 06:00 UTC. The snapshot date is the logical date (manual runs: run time).
  Re-running a date replaces its raw files and bronze rows.

**load_bronze** writes `iceberg.bronze.fcdo_advice`: one row per country with title,
`public_updated_at`, `change_description`, `alert_status_json` and `parts_json` (sections with the
**HTML unchanged**; text cleaning is silver's job).

**License:** Contains public sector information licensed under the Open Government Licence v3.0.

Code lives in the `ingestion` package (`ingestion.sources.fcdo`).
"""


@dag(
    dag_id="fcdo_advice",
    schedule="0 6 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "advisories", "fcdo"],
    doc_md=DOC_MD,
)
def fcdo_advice():
    def snapshot_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    @task
    def ingest() -> dict:
        from ingestion.sources.fcdo import ingest_snapshot

        return ingest_snapshot(snapshot_date(), bucket=RAW_BUCKET)

    @task(outlets=[FCDO_ADVICE])
    def load_bronze() -> dict:
        from ingestion.sources.fcdo import load_bronze as load

        run_id = get_current_context()["dag_run"].run_id
        return load(snapshot_date(), dag_run_id=run_id, bucket=RAW_BUCKET)

    ingest() >> load_bronze()


fcdo_advice()
