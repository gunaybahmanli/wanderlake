"""dbt silver and gold models, orchestrated by Astronomer Cosmos and scheduled by Airflow Assets."""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.sdk import dag, get_current_context, task
from airflow.timetables.assets import AssetOrTimeSchedule
from airflow.timetables.trigger import CronTriggerTimetable
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import (
    ExecutionMode,
    InvocationMode,
    LoadMode,
    TestBehavior,
    TestIndirectSelection,
)
from cosmos.operators.local import DbtSourceLocalOperator
from wanderlake_assets import (
    CBAR_RATES,
    FRANKFURTER_RATES,
    GOLD,
    OPEN_METEO_FORECAST,
    OPEN_METEO_HISTORY,
    WIKIMEDIA_CITY_IMAGES,
)

log = logging.getLogger(__name__)

# Paths come from the environment (compose sets them in the container; CI sets its own).
DBT_PROJECT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", "/opt/wanderlake/dbt"))
DBT_PROFILES_DIR = Path(os.environ.get("DBT_PROFILES_DIR", str(DBT_PROJECT_DIR)))
DBT_MANIFEST_PATH = Path(
    os.environ.get("DBT_MANIFEST_PATH", str(DBT_PROJECT_DIR / "target" / "manifest.json"))
)
DBT_EXECUTABLE = os.environ.get("DBT_EXECUTABLE_PATH", "/opt/dbt-venv/bin/dbt")

# The 18:00 UTC fallback skips when gold was already built within this window.
FALLBACK_SKIP_WINDOW = timedelta(hours=20)

if not DBT_MANIFEST_PATH.is_file():
    # Fail the DAG import with a clear message instead of letting Cosmos run dbt while parsing.
    raise FileNotFoundError(
        f"dbt manifest not found at {DBT_MANIFEST_PATH}: run 'make dbt-manifest' (dbt parse)."
    )

DOC_MD = """
### wanderlake_transform: dbt silver and gold (Cosmos)

Builds the dbt project (`dbt/`) as Airflow tasks with
[Astronomer Cosmos](https://github.com/astronomer/astronomer-cosmos): one task per seed and
model, each followed by its own tests (`TestBehavior.AFTER_EACH`), so a failing test stops only
what depends on that model, and a retry reruns only that model.

**Order:** `gate` >> `source_freshness` >> `seeds` >> `models` >> `publish_gold`

- **gate:** lets asset-triggered and manual runs through. A run from the 18:00 fallback is
  skipped when the `gold` asset was updated in the last 20 hours (the daily build already ran).
- **source_freshness:** `dbt source freshness`. Warnings are logged and the run continues;
  errors (a source past its `error_after`) fail the run before any model is built.
- **seeds** (`resource_type:seed`), then **models** (models and tests: staging views, silver and
  gold tables; tests on several nodes run as their own task after their parents). Cosmos reads
  the dependency graph from a pre-built `manifest.json`, so parsing this DAG never runs dbt:
  **run `make dbt-manifest` after changing models**, or the DAG keeps the old graph.
- **publish_gold:** records an event on the `gold` asset (`iceberg://wanderlake/gold`), for
  consumers such as the website and dashboards, and for the gate above.

**Schedule (data-aware, Airflow Assets):** `AssetOrTimeSchedule` = a run when **all four** daily
inputs have new asset events since the last asset-triggered run
(`bronze.frankfurter_rates`, `bronze.cbar_rates`, `bronze.open_meteo_forecast`,
`bronze.open_meteo_history`; each written by its ingestion DAG's load task), **or** when the
weekly `bronze.wikimedia_city_images` is updated (on its own, so new city images reach the
website the same day), **or** at **18:00 UTC** every day as a fallback, so the models still
refresh when a source fails or has nothing new. On a normal day the asset condition completes
at about 16:35 UTC (after the 16:30 rate DAGs) and the 18:00 run is skipped by the gate.
Weekly, monthly and FCDO sources (`rest_countries`, `geonames_cities`, `fcdo_advice`,
`config_cities`) also publish assets but are not in the condition: they never block the daily
run, which reads their latest state. The city images trigger a run but never block one (OR).

**Runs on:** Airflow and Trino together (`make mode-full`); dbt runs in its own virtualenv
(`/opt/dbt-venv`, ADR-0007) as a subprocess, one task at a time (`PARALLELISM=1`).
See [ADR-0009](docs/adr/0009-orchestrate-dbt-with-cosmos-and-assets.md).
"""

PROFILE_CONFIG = ProfileConfig(
    profile_name="wanderlake",
    target_name="local",
    profiles_yml_filepath=DBT_PROFILES_DIR / "profiles.yml",
)
PROJECT_CONFIG = ProjectConfig(
    dbt_project_path=DBT_PROJECT_DIR,
    manifest_path=DBT_MANIFEST_PATH,
    project_name="wanderlake",
    install_dbt_deps=False,  # dbt_packages/ comes from make dbt-deps; no network per task
)


def execution_config(indirect_selection: TestIndirectSelection) -> ExecutionConfig:
    return ExecutionConfig(
        execution_mode=ExecutionMode.LOCAL,
        invocation_mode=InvocationMode.SUBPROCESS,  # dbt is not importable from Airflow's Python
        dbt_executable_path=DBT_EXECUTABLE,
        test_indirect_selection=indirect_selection,
    )


def render_config(*, detach_multi_parent_tests: bool, **selection: list[str]) -> RenderConfig:
    return RenderConfig(
        load_method=LoadMode.DBT_MANIFEST,
        test_behavior=TestBehavior.AFTER_EACH,
        emit_datasets=False,  # one explicit gold asset instead of one per model
        enable_owner_inheritance=False,  # every task keeps owner "wanderlake"
        should_detach_multiple_parents_tests=detach_multi_parent_tests,
        **selection,
    )


def should_build(run_type: str, last_gold: datetime | None, now: datetime) -> bool:
    """Gate rule: only a time-scheduled (fallback) run can skip, when gold is recent enough."""
    if run_type != "scheduled":  # asset_triggered, manual, backfill: always build
        return True
    return last_gold is None or now - last_gold > FALLBACK_SKIP_WINDOW


def log_freshness_warnings(context: dict) -> None:
    log.warning("dbt source freshness warnings: %s", context.get("test_names"))


@dag(
    dag_id="wanderlake_transform",
    schedule=AssetOrTimeSchedule(
        timetable=CronTriggerTimetable("0 18 * * *", timezone="UTC"),
        # All four daily inputs, or the weekly city images on their own.
        assets=(
            (FRANKFURTER_RATES & CBAR_RATES & OPEN_METEO_FORECAST & OPEN_METEO_HISTORY)
            | WIKIMEDIA_CITY_IMAGES
        ),
    ),
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["transform", "dbt", "cosmos"],
    doc_md=DOC_MD,
)
def wanderlake_transform():
    @task.short_circuit(inlets=[GOLD])
    def gate() -> bool:
        context = get_current_context()
        # A str enum (DagRunType); str() would give "DagRunType.SCHEDULED", so compare the value.
        run_type = getattr(context["dag_run"].run_type, "value", context["dag_run"].run_type)
        last_gold = None
        if run_type == "scheduled":
            with contextlib.suppress(IndexError):  # IndexError: no gold event yet
                last_gold = context["inlet_events"][GOLD][-1].timestamp
        build = should_build(run_type, last_gold, datetime.now(UTC))
        log.info("Run type %s, gold last updated at %s: build=%s.", run_type, last_gold, build)
        return build

    source_freshness = DbtSourceLocalOperator(
        task_id="source_freshness",
        project_dir=str(DBT_PROJECT_DIR),
        profile_config=PROFILE_CONFIG,
        dbt_executable_path=DBT_EXECUTABLE,
        invocation_mode=InvocationMode.SUBPROCESS,
        install_deps=False,
        on_warning_callback=log_freshness_warnings,
    )

    seeds = DbtTaskGroup(
        group_id="seeds",
        project_config=PROJECT_CONFIG,
        profile_config=PROFILE_CONFIG,
        # Cautious: only the seed's own tests. Tests from a model to a seed (weather codes) are
        # rendered in the models group, after that model.
        execution_config=execution_config(TestIndirectSelection.CAUTIOUS),
        render_config=render_config(detach_multi_parent_tests=False, select=["resource_type:seed"]),
    )

    models = DbtTaskGroup(
        group_id="models",
        project_config=PROJECT_CONFIG,
        profile_config=PROFILE_CONFIG,
        # Tests on several nodes (e.g. dim_city + fct_exchange_rate, or a model and a seed) get
        # their own task after their model parents. Select models and tests rather than exclude
        # seeds: Cosmos passes exclusions to every dbt command, and dbt would then drop the tests
        # that touch a seed as well.
        execution_config=execution_config(TestIndirectSelection.EAGER),
        render_config=render_config(
            detach_multi_parent_tests=True, select=["resource_type:model", "resource_type:test"]
        ),
    )

    @task(outlets=[GOLD])
    def publish_gold() -> None:
        log.info("Gold layer built: recorded an event on asset %s.", GOLD.uri)

    gate() >> source_freshness >> seeds >> models >> publish_gold()


wanderlake_transform()
