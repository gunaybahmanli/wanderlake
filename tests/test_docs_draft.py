"""make docs-draft and make docs-check (ADR-0013): finding gaps, Ollama, YAML edits, the guard."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
import requests_mock as rm

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str) -> Any:
    """Import a module by path: adding the repo root to sys.path would make the repo's airflow/
    folder shadow the airflow package (the DAG tests skip only when it is missing)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


docs_draft = load("wanderlake_docs_draft", "ai/docs_draft.py")
check_draft_docs = load("wanderlake_check_draft_docs", "ai/check_draft_docs.py")
mcp_check = load("wanderlake_mcp_check", "scripts/mcp_check.py")
DocsDraftError, Target = docs_draft.DocsDraftError, docs_draft.Target

# --- Ollama address and model -------------------------------------------------------------------


def test_default_gateway_is_read_from_the_route_table(tmp_path: Path) -> None:
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\n"
        "eth0\t00000000\t01F01AAC\t0003\n"  # 172.26.240.1, little-endian hex
        "eth0\t00F01AAC\t00000000\t0001\n"
    )
    assert docs_draft.default_gateway(route) == "172.26.240.1"
    assert docs_draft.default_gateway(tmp_path / "missing") is None


def test_ollama_candidates() -> None:
    assert docs_draft.ollama_candidates({}, "172.26.240.1") == [
        "http://localhost:11434",  # WSL mirrored networking
        "http://172.26.240.1:11434",  # WSL NAT: the Windows host
    ]
    assert docs_draft.ollama_candidates({"OLLAMA_HOST": "10.0.0.5"}, "x") == [
        "http://10.0.0.5:11434"
    ]
    assert docs_draft.ollama_candidates({"OLLAMA_HOST": "https://h:8000/"}, None) == [
        "https://h:8000"
    ]


def test_find_ollama_skips_hosts_that_do_not_answer(requests_mock: rm.Mocker) -> None:
    requests_mock.get("http://localhost:11434/api/version", exc=requests.ConnectionError)
    requests_mock.get("http://gw:11434/api/version", json={"version": "0.40.18"})
    requests_mock.get("http://gw:11434/api/tags", json={"models": [{"name": "llama3.2:3b"}]})
    hosts = ["http://localhost:11434", "http://gw:11434"]
    assert docs_draft.find_ollama("llama3.2:3b", hosts) == "http://gw:11434"
    with pytest.raises(DocsDraftError, match="ollama pull qwen3:8b"):
        docs_draft.find_ollama("qwen3:8b", hosts)


def test_find_ollama_fails_clearly_when_not_running(requests_mock: rm.Mocker) -> None:
    requests_mock.get("http://gw:11434/api/version", exc=requests.ConnectionError)
    with pytest.raises(DocsDraftError, match=r"not reachable \(tried http://gw:11434\).*Windows"):
        docs_draft.find_ollama("llama3.2:3b", ["http://gw:11434"])


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("date of the snapshot", "Date of the snapshot."),
        ("  Rate per   EUR. ", "Rate per EUR."),
        ("Draft: rate per EUR", None),  # never echo the marker
        ("Rate.", None),  # too short to be useful
        ("x " * 150, None),  # too long
        (42, None),
    ],
)
def test_clean(answer: Any, expected: str | None) -> None:
    assert docs_draft.clean(answer) == expected


# --- what is missing ----------------------------------------------------------------------------


def manifest() -> dict[str, Any]:
    model = {
        "unique_id": "model.wanderlake.m",
        "resource_type": "model",
        "package_name": "wanderlake",
        "name": "m",
        "patch_path": "wanderlake://models/x.yml",
        "database": "iceberg",
        "schema": "silver",
        "alias": "m",
        "raw_code": "select 1",
        "description": "A model with a proper description.",
        "columns": {
            "a": {"description": "Fine description here."},
            "b": {"description": ""},
            "c": {"description": "Short."},
        },
    }
    source = {
        "unique_id": "source.wanderlake.bronze.t",
        "resource_type": "source",
        "package_name": "wanderlake",
        "name": "t",
        "source_name": "bronze",
        "original_file_path": "models/sources.yml",
        "database": "iceberg",
        "schema": "bronze",
        "identifier": "t",
        "description": "",
        "columns": {},
    }
    return {
        "metadata": {"project_name": "wanderlake"},
        "nodes": {model["unique_id"]: model},
        "sources": {source["unique_id"]: source},
    }


def test_find_targets_drafts_only_empty_or_missing() -> None:
    columns = {
        ("silver", "m"): [("a", "varchar"), ("b", "date"), ("c", "bigint"), ("d", "double")],
        ("bronze", "t"): [("x", "varchar")],
    }
    targets, short = docs_draft.find_targets(manifest(), columns)
    by_name = {t.name: t for t in targets}
    assert by_name["m"].columns == {"b": "date", "d": "double"}  # empty and undeclared
    assert not by_name["m"].draft_node  # the model has a description
    assert by_name["t"].draft_node and by_name["t"].columns == {"x": "varchar"}
    assert short == ["m.c: 'Short.'"]  # listed, never drafted
    assert [t.name for t in docs_draft.find_targets(manifest(), columns, ["t"])[0]] == ["t"]


# --- writing the YAML ---------------------------------------------------------------------------

YAML_IN = """\
version: 2

models:
  - name: block_model
    description: >
      Kept as it is.
    columns:
      - name: a
        description: Existing text.  # a comment that must stay
      - name: b
        data_tests: [not_null]
      - name: e
        description: ""
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [a, b]

  - name: flow_model
    description: Flow columns.
    columns:
      - {name: a, data_type: varchar, description: Existing., constraints: [{type: not_null}]}
      - {name: b, data_type: date}

  - name: bare_model
    description: No columns yet.
"""

YAML_OUT = """\
version: 2

models:
  - name: block_model
    description: >
      Kept as it is.
    columns:
      - name: a
        description: Existing text.  # a comment that must stay
      - name: b
        description: "(draft) B text."
        data_tests: [not_null]
      - name: e
        description: "(draft) E text."
      - name: c
        description: "(draft) C \\"quoted\\" text."
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [a, b]

  - name: flow_model
    description: Flow columns.
    columns:
      - {name: a, data_type: varchar, description: Existing., constraints: [{type: not_null}]}
      - {name: b, description: "(draft) Flow b.", data_type: date}
      - {name: c, description: "(draft) Flow c."}

  - name: bare_model
    description: No columns yet.
    columns:
      - name: x
        description: "(draft) X text."
"""


def target(name: str, path: Path) -> Target:
    return Target("model", name, None, path, ("iceberg", "s", name), "", "", False)


def test_write_drafts_edits_only_what_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "models.yml"
    path.write_text(YAML_IN)
    docs_draft.write_drafts(
        path,
        [
            (
                target("block_model", path),
                None,
                {"a": "Must not replace.", "b": "B text.", "e": "E text.", "c": 'C "quoted" text.'},
            ),
            (
                target("flow_model", path),
                None,
                {"a": "Must not replace.", "b": "Flow b.", "c": "Flow c."},
            ),
            (target("bare_model", path), None, {"x": "X text."}),
        ],
    )
    assert path.read_text() == YAML_OUT
    assert check_draft_docs.find_markers(tmp_path)  # the guard sees them


def test_write_drafts_adds_a_missing_node_description(tmp_path: Path) -> None:
    path = tmp_path / "sources.yml"
    path.write_text(
        "version: 2\n\nsources:\n  - name: bronze\n    tables:\n      - name: t\n"
        "        columns:\n          - name: x\n            description: X.\n"
    )
    src = Target("source", "t", "bronze", path, ("iceberg", "bronze", "t"), "", "", True)
    docs_draft.write_drafts(path, [(src, "Raw rows of t.", {})])
    assert (
        '      - name: t\n        description: "(draft) Raw rows of t."\n        columns:'
        in path.read_text()
    )


def test_the_prompt_carries_types_and_samples() -> None:
    t = Target(
        "model",
        "m",
        None,
        Path("x"),
        ("iceberg", "silver", "m"),
        "select 1 as b",
        "A model.",
        False,
    )
    prompt = docs_draft.build_prompt(t, {"b": "date"}, {"b": ["2026-11-20", "2026-11-21"]})
    assert "- b (date); sample values: 2026-11-20, 2026-11-21" in prompt
    assert "select 1 as b" in prompt and 'Leave "description" empty' in prompt


def test_samples_are_read_with_a_small_limit() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("a", 1), ("a", None), ("b", 2), ("c", 3), ("d", 4)]

    cur = Cursor()
    conn = type("Conn", (), {"cursor": lambda self: cur})()
    samples = docs_draft.sample_values(conn, ("iceberg", "silver", "m"), ["x", "y"])
    assert cur.sql == 'select "x", "y" from "iceberg"."silver"."m" limit 5'
    assert samples == {"x": ["a", "b", "c"], "y": ["1", "2", "3"]}


# --- the guard ----------------------------------------------------------------------------------


def test_docs_check_finds_markers_and_skips_packages(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "dbt_packages/pkg").mkdir(parents=True)
    (tmp_path / "models/a.yml").write_text("models:\n  - name: m\n    description: Reviewed.\n")
    (tmp_path / "dbt_packages/pkg/b.yml").write_text('description: "(draft) theirs"\n')
    assert check_draft_docs.main([str(tmp_path)]) == 0
    (tmp_path / "models/a.yml").write_text('models:\n  - name: m\n    description: "(draft) X."\n')
    assert check_draft_docs.find_markers(tmp_path) == [
        f'{tmp_path.name}/models/a.yml:3: description: "(draft) X."'
    ]
    assert check_draft_docs.main([str(tmp_path)]) == 1


def test_docs_check_runs_in_ci() -> None:
    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    steps = [s.get("run", "") for s in jobs["docs-check"]["steps"]]
    assert any("ai.check_draft_docs" in s or "make docs-check" in s for s in steps)


# --- dbt MCP server: read-only ------------------------------------------------------------------


def test_mcp_server_is_read_only() -> None:
    config = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["dbt"]
    assert config == {"command": "bash", "args": ["scripts/dbt-mcp.sh"]}  # no secrets, no paths
    wrapper = (ROOT / "scripts/dbt-mcp.sh").read_text()
    enabled = re.search(r'DBT_MCP_ENABLE_TOOLS="([^"]+)"', wrapper)
    assert enabled and set(enabled.group(1).split(",")) == mcp_check.READ_ONLY_TOOLS
    assert not mcp_check.READ_ONLY_TOOLS & mcp_check.WRITE_TOOLS
    assert "export DBT_TARGET=local" in wrapper  # never the prod target
    assert "export DBT_TRINO_USER=wanderlake_mcp" in wrapper
    version = re.search(r"DBT_MCP_VERSION=(\S+)", wrapper)
    makefile = re.search(r"DBT_MCP_VERSION := (\S+)", (ROOT / "Makefile").read_text())
    assert version and makefile and version.group(1) == makefile.group(1)


def test_trino_user_of_the_ai_tools_is_read_only() -> None:
    rules = json.loads((ROOT / "infra/compose/trino/etc/rules.json").read_text())["catalogs"]
    assert rules[0] == {"user": "wanderlake_mcp", "catalog": ".*", "allow": "read-only"}
    assert rules[-1] == {"catalog": ".*", "allow": "all"}  # everyone else unchanged
    compose = (ROOT / "infra/compose/compose.yaml").read_text()
    assert "/etc/trino/access-control.properties:ro" in compose
    assert "/etc/trino/rules.json:ro" in compose
    assert docs_draft.TRINO_USER == "wanderlake_mcp"
