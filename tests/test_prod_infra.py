"""Static checks of the production setup (ADR-0012): workflow, prod Trino and the catalog DDL."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/prod-pipeline.yml"
CATALOG = ROOT / "infra/compose/trino/catalog/iceberg.properties"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_workflow_runs_only_on_schedule_and_dispatch() -> None:
    triggers = workflow()[True]  # YAML 1.1 reads the key "on" as True
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "15 17 * * *"}]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"mode", "date", "start", "end", "only"}
    # "only" offers exactly the runner's backfill parts (python -m ingestion.run backfill --only).
    from ingestion.pipeline import BACKFILL_PARTS

    assert inputs["only"]["options"] == ["all", *BACKFILL_PARTS]


def test_workflow_is_a_single_writer_with_least_privilege() -> None:
    wf = workflow()
    assert wf["concurrency"] == {"group": "prod-pipeline", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    assert job["environment"] == "production"
    assert "timeout-minutes" in job


def test_workflow_transforms_and_publishes_in_the_right_order() -> None:
    wf = workflow()
    assert wf["env"]["WANDERLAKE_ENV"] == "prod"  # the only environment that publishes
    assert wf["env"]["DBT_TARGET"] == "prod"
    (job,) = wf["jobs"].values()
    steps = {s.get("id"): s for s in job["steps"] if s.get("id")}
    order = [s.get("id") for s in job["steps"] if s.get("id")]
    assert order == ["record_start", "trino", "bootstrap", "ingest", "dbt", "publish"]
    # dbt runs even when a source failed (the job still fails); publish only after dbt passed.
    assert "steps.bootstrap.outcome == 'success'" in steps["dbt"]["if"]
    assert "steps.dbt.outcome == 'success'" in steps["publish"]["if"]
    assert "python -m ingestion.publish" in steps["publish"]["run"]
    finish = next(s for s in job["steps"] if "ingestion.publish.runs finish" in s.get("run", ""))
    assert finish["if"] == "always()"
    assert finish["env"]["STEPS_JSON"] == "${{ toJSON(steps) }}"


def test_workflow_actions_are_pinned_to_commits() -> None:
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW.read_text())
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_workflow_scripts_do_not_interpolate_inputs_or_secrets() -> None:
    (job,) = workflow()["jobs"].values()
    for step in job["steps"]:
        script = step.get("run", "")
        assert "${{" not in script, step.get("name")


def test_trino_catalog_is_configured_by_environment_only() -> None:
    props = dict(
        line.split("=", 1)
        for line in CATALOG.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    assert props["iceberg.jdbc-catalog.connection-url"] == "${ENV:ICEBERG_CATALOG_JDBC_URL}"
    assert props["s3.endpoint"] == "${ENV:S3_ENDPOINT}"
    assert props["iceberg.jdbc-catalog.catalog-name"] == "${ENV:ICEBERG_CATALOG_NAME}"
    compose = yaml.safe_load((ROOT / "infra/compose/compose.yaml").read_text())
    trino_env = compose["services"]["trino"]["environment"]
    assert trino_env["ICEBERG_CATALOG_JDBC_URL"].startswith("jdbc:postgresql://postgres:5432/")
    assert trino_env["S3_ENDPOINT"] == "http://seaweedfs:8333"


def _size_gb(value: str) -> float:
    number, unit = re.fullmatch(r"(\d+)\s*([GM])B?", value).groups()
    return int(number) / (1024 if unit == "M" else 1)


@pytest.mark.parametrize("etc", ["infra/compose/trino/etc", "infra/prod/trino/etc"])
def test_trino_memory_fits_the_heap(etc: str) -> None:
    config = dict(
        line.split("=", 1)
        for line in (ROOT / etc / "config.properties").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    jvm = (ROOT / etc / "jvm.config").read_text()
    heap = _size_gb(re.search(r"-Xmx(\d+[GM])", jvm).group(1))
    per_node = _size_gb(config["query.max-memory-per-node"])
    headroom = _size_gb(config["memory.heap-headroom-per-node"])
    assert per_node + headroom < heap


def _create_tables(text: str) -> list[str]:
    statements = re.findall(r"CREATE TABLE IF NOT EXISTS .*?\);", text, flags=re.S)
    return [re.sub(r"\s+", " ", s) for s in statements]


def test_prod_catalog_tables_match_dev() -> None:
    dev = _create_tables((ROOT / "infra/compose/postgres/init/02-iceberg-catalog.sh").read_text())
    prod = _create_tables((ROOT / "infra/prod/catalog-init.sh").read_text())
    assert len(dev) == 2
    assert prod == dev


def test_bootstrap_rejects_a_warehouse_that_is_not_a_plain_uri() -> None:
    script = ROOT / "infra/compose/trino/sql/bootstrap.sh"
    result = subprocess.run(
        ["bash", str(script)],
        env={
            "ICEBERG_WAREHOUSE": "s3://lake/x'; drop schema iceberg.bronze; --",
            "PATH": "/usr/bin",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "must look like s3://bucket/prefix" in result.stderr
