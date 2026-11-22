"""Copy config/cities.yaml into iceberg.bronze.config_cities (daily, and on demand)."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import CONFIG_CITIES

DOC_MD = """
### Cities config to bronze

`config/cities.yaml` is the single source of truth for our cities. This DAG validates it and
**replaces** `iceberg.bronze.config_cities` with its current content (full replace, idempotent),
so SQL (dbt sources, Trino) can use it. No raw copy: the file is versioned in git;
`_source_file` holds its path and sha256.

- **Schedule:** daily at 00:30 UTC; trigger it manually after changing the file
  (or run `make config-load`).
- **Validation:** the same checks as `ingestion.config.load_cities()` (ids, codes, coordinates,
  time zones, geonames_id). An invalid file fails the task and the table stays as it was.

Code lives in `ingestion.sources.config_cities`.
"""


@dag(
    dag_id="config_cities",
    schedule="30 0 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "config"],
    doc_md=DOC_MD,
)
def config_cities():
    @task(outlets=[CONFIG_CITIES])
    def load() -> dict:
        from ingestion.sources.config_cities import load as load_config

        return load_config(dag_run_id=get_current_context()["dag_run"].run_id)

    load()


config_cities()
