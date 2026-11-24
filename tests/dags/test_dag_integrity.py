"""DAG integrity: every DAG imports cleanly and follows the project's rules.

Runs only where Airflow is installed (CI "dag-integrity" job, or `make test-dags` inside the
Airflow container). Plain `make test` skips this module.
"""

from __future__ import annotations

import inspect
import os
from functools import cache
from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.dag_processing.dagbag import DagBag  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DAGS_FOLDER = Path(os.environ.get("DAGS_FOLDER", REPO_ROOT / "airflow" / "dags"))
PLACEHOLDER_OWNER = "airflow"  # Airflow's default when no owner is set


@cache
def _load_dagbag() -> DagBag:
    kwargs = {}
    # Older Airflow versions need include_examples=False. Airflow 3.4.0 no longer has the
    # argument: example DAGs live in their own bundle, so a folder DagBag loads only that folder.
    if "include_examples" in inspect.signature(DagBag.__init__).parameters:
        kwargs["include_examples"] = False
    return DagBag(dag_folder=str(DAGS_FOLDER), **kwargs)


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return _load_dagbag()


def _dag_ids() -> list[str]:
    return sorted(_load_dagbag().dag_ids)


def test_no_import_errors(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}, "DAG import errors:\n" + "\n".join(
        f"{path}: {error}" for path, error in dagbag.import_errors.items()
    )


def test_dags_found(dagbag: DagBag) -> None:
    assert dagbag.dag_ids, f"no DAGs found in {DAGS_FOLDER}"


@pytest.mark.parametrize("dag_id", _dag_ids())
class TestDagRules:
    def test_has_tags(self, dagbag: DagBag, dag_id: str) -> None:
        assert dagbag.dags[dag_id].tags, f"{dag_id}: add at least one tag"

    def test_catchup_disabled(self, dagbag: DagBag, dag_id: str) -> None:
        assert dagbag.dags[dag_id].catchup is False, f"{dag_id}: set catchup=False"

    def test_default_retries(self, dagbag: DagBag, dag_id: str) -> None:
        retries = dagbag.dags[dag_id].default_args.get("retries", 0)
        assert retries >= 1, f"{dag_id}: set default_args['retries'] >= 1 (got {retries})"

    def test_owner_set(self, dagbag: DagBag, dag_id: str) -> None:
        dag = dagbag.dags[dag_id]
        owner = dag.default_args.get("owner")
        assert owner and owner != PLACEHOLDER_OWNER, (
            f"{dag_id}: set default_args['owner'] (got {owner!r})"
        )
        for task in dag.tasks:
            assert task.owner == owner, f"{dag_id}.{task.task_id}: owner {task.owner!r}"


class TestWanderlakeTransform:
    """Cosmos DAG: asset-or-time schedule, freshness first, seeds before models, tests per model."""

    DAG_ID = "wanderlake_transform"

    def test_schedule_is_assets_or_daily_time(self, dagbag: DagBag) -> None:
        from airflow.timetables.assets import AssetOrTimeSchedule

        timetable = dagbag.dags[self.DAG_ID].timetable
        assert isinstance(timetable, AssetOrTimeSchedule)
        assert "0 18 * * *" in timetable.summary

    def test_inputs_trigger_it(self, dagbag: DagBag) -> None:
        """(all four daily inputs) OR the weekly city images."""
        from wanderlake_assets import DAILY_TRANSFORM_INPUTS, STANDALONE_TRANSFORM_INPUTS

        def kind(obj: object) -> str:  # DagBag may hold serialized classes (SerializedAssetAny)
            return type(obj).__name__.removeprefix("Serialized")

        condition = dagbag.dags[self.DAG_ID].timetable.asset_condition
        uris = {asset.uri for _, asset in condition.iter_assets()}
        assert uris == {a.uri for a in DAILY_TRANSFORM_INPUTS + STANDALONE_TRANSFORM_INPUTS}
        assert kind(condition) == "AssetAny"
        [all_daily] = [o for o in condition.objects if kind(o) == "AssetAll"]
        assert {a.uri for a in all_daily.objects} == {a.uri for a in DAILY_TRANSFORM_INPUTS}
        standalone = {o.uri for o in condition.objects if kind(o) == "Asset"}
        assert standalone == {a.uri for a in STANDALONE_TRANSFORM_INPUTS}

    def test_every_input_has_a_producer(self, dagbag: DagBag) -> None:
        from wanderlake_assets import DAILY_TRANSFORM_INPUTS, STANDALONE_TRANSFORM_INPUTS

        produced = {
            outlet.uri
            for dag in dagbag.dags.values()
            for task in dag.tasks
            for outlet in (task.outlets or [])
        }
        inputs = DAILY_TRANSFORM_INPUTS + STANDALONE_TRANSFORM_INPUTS
        missing = {asset.uri for asset in inputs} - produced
        assert not missing, f"no task declares these assets as outlets: {missing}"

    def test_order_and_tasks(self, dagbag: DagBag) -> None:
        dag = dagbag.dags[self.DAG_ID]
        ids = [task.task_id for task in dag.topological_sort()]
        assert ids[:2] == ["gate", "source_freshness"]
        assert ids[-1] == "publish_gold"
        seed_tasks = [i for i, task_id in enumerate(ids) if task_id.startswith("seeds.")]
        model_tasks = [i for i, task_id in enumerate(ids) if task_id.startswith("models.")]
        assert seed_tasks and model_tasks and max(seed_tasks) < min(model_tasks)
        assert "models.city_overview.run" in ids and "models.city_overview.test" in ids


class TestPublishCloud:
    """Runs after every transform (gold asset), migrations first, one task per table, verify."""

    DAG_ID = "publish_cloud"

    def test_scheduled_only_by_the_gold_asset(self, dagbag: DagBag) -> None:
        from wanderlake_assets import GOLD

        dag = dagbag.dags[self.DAG_ID]
        # Compared by name: the class lives in the SDK or core module depending on the version.
        assert type(dag.timetable).__name__ == "AssetTriggeredTimetable"
        assert {a.uri for a in dag.timetable.asset_condition.objects} == {GOLD.uri}
        transform = dagbag.dags[TestWanderlakeTransform.DAG_ID]
        assert any(GOLD.uri in {o.uri for o in (t.outlets or [])} for t in transform.tasks)

    def test_rules(self, dagbag: DagBag) -> None:
        dag = dagbag.dags[self.DAG_ID]
        assert set(dag.tags) == {"publish", "cloud"}
        assert dag.default_args["owner"] == "wanderlake"
        assert dag.default_args["retries"] == 2
        assert dag.doc_md

    def test_order(self, dagbag: DagBag) -> None:
        dag = dagbag.dags[self.DAG_ID]
        ids = [task.task_id for task in dag.topological_sort()]
        assert ids == ["apply_migrations", "publish_table", "verify_counts"]
        assert dag.get_task("verify_counts").outlets[0].uri == "supabase://wanderlake/api"


def test_gate_skips_only_a_fallback_run_after_a_recent_build() -> None:
    from datetime import UTC, datetime, timedelta

    from wanderlake_transform import should_build

    now = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
    built_today = now - timedelta(hours=1, minutes=25)  # asset-triggered run at 16:35
    built_yesterday = now - timedelta(hours=25)
    assert should_build("scheduled", built_today, now) is False
    assert should_build("scheduled", built_yesterday, now) is True
    assert should_build("scheduled", None, now) is True
    for run_type in ("asset_triggered", "manual", "backfill"):
        assert should_build(run_type, built_today, now) is True
