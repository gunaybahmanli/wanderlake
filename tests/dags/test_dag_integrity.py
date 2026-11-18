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
