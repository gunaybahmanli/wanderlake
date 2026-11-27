"""Draft missing dbt descriptions with a local Ollama model, for a human to review (ADR-0013).

    make docs-draft                      # all models, seeds and sources
    make docs-draft ARGS="--select dim_city --dry-run"

Finds models, seeds, source tables and columns whose description is empty or missing (including
columns that exist in Trino but are not in the YAML yet), and asks a local Ollama model for a
one-sentence draft from the name, the model SQL, the column types and a few sample values. Each
draft is written to the YAML file with the prefix "(draft) ": a human reviews it, edits it if
needed, and removes the marker. make docs-check (and CI) fails while any marker remains.

Never overwrites a non-empty description. Very short ones (under SHORT_CHARS) are only listed in
the report. The YAML files are edited in place line by line (ruamel.yaml only locates entries),
so every other byte stays as it was.

Reads Trino as the read-only user wanderlake_mcp (infra/compose/trino/etc/rules.json), with
small LIMITs. Ollama runs on Windows: from WSL it is reached at OLLAMA_HOST if set, otherwise
localhost (WSL mirrored networking) or the Windows host, which is WSL's default gateway (NAT).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"
MARKER = "(draft) "
SHORT_CHARS = 15
DEFAULT_MODEL = "llama3.2:3b"
OLLAMA_PORT = 11434
TRINO_USER = "wanderlake_mcp"
SAMPLE_ROWS = 5
SAMPLE_VALUES = 3
SQL_CHARS = 4000
COLUMNS_PER_REQUEST = 12
MAX_DESCRIPTION_CHARS = 200


class DocsDraftError(RuntimeError):
    """A problem the user has to fix (Ollama not running, Trino down, model not pulled...)."""


# --- what is missing -------------------------------------------------------------------------


@dataclass
class Target:
    """One model, seed or source table with something to draft."""

    kind: str  # model, seed or source
    name: str  # model/seed name, or source table name
    source_name: str | None  # for sources: the source (e.g. bronze)
    yaml_path: Path
    relation: tuple[str, str, str]  # catalog, schema, table in Trino
    sql: str
    description: str  # existing node description ("" if missing)
    draft_node: bool  # the node's own description is missing
    columns: dict[str, str] = field(default_factory=dict)  # column -> Trino type, to draft
    short: list[str] = field(default_factory=list)  # existing descriptions that are very short


def is_missing(text: str | None) -> bool:
    return not (text or "").strip()


def is_short(text: str | None) -> bool:
    return not is_missing(text) and len((text or "").strip()) < SHORT_CHARS


def find_targets(
    manifest: dict[str, Any],
    trino_columns: dict[tuple[str, str], list[tuple[str, str]]],
    select: Sequence[str] = (),
) -> tuple[list[Target], list[str]]:
    """Targets with something to draft, plus "node.column: text" lines for short descriptions.
    trino_columns maps (schema, table) to [(column, type)] in table order."""
    targets, short = [], []
    nodes = [
        n
        for n in list(manifest["nodes"].values()) + list(manifest["sources"].values())
        if n["resource_type"] in ("model", "seed", "source")
        and n.get("package_name", "wanderlake")
        == manifest["metadata"].get("project_name", "wanderlake")
    ]
    for node in sorted(nodes, key=lambda n: n["unique_id"]):
        if select and node["name"] not in select:
            continue
        kind = node["resource_type"]
        yaml_file = node.get("original_file_path") if kind == "source" else node.get("patch_path")
        if not yaml_file:
            short.append(f"{node['name']}: no YAML entry (not drafted; add one by hand)")
            continue
        yaml_path = DBT_DIR / yaml_file.split("://", 1)[-1]
        table = node.get("identifier") or node.get("alias") or node["name"]
        documented = {k.lower(): (v.get("description") or "") for k, v in node["columns"].items()}
        target = Target(
            kind=kind,
            name=node["name"],
            source_name=node.get("source_name"),
            yaml_path=yaml_path,
            relation=(node["database"], node["schema"], table),
            sql=(node.get("raw_code") or "")[:SQL_CHARS],
            description=(node.get("description") or "").strip(),
            draft_node=is_missing(node.get("description")),
        )
        label = f"{node['source_name']}.{node['name']}" if kind == "source" else node["name"]
        if is_short(node.get("description")):
            short.append(f"{label}: {node['description'].strip()!r}")
        for column, ctype in trino_columns.get((node["schema"], table), []):
            text = documented.get(column.lower())
            if is_missing(text):
                target.columns[column] = ctype
            elif is_short(text):
                short.append(f"{label}.{column}: {text.strip()!r}")
        if target.draft_node or target.columns:
            targets.append(target)
    return targets, short


# --- Trino (read-only) -----------------------------------------------------------------------


def trino_connect(host: str, port: int) -> Any:
    import trino

    return trino.dbapi.connect(host=host, port=port, user=TRINO_USER, catalog="iceberg")


def read_trino_columns(
    conn: Any, schemas: Iterable[str]
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    names = sorted(set(schemas))
    placeholders = ", ".join("?" for _ in names)
    cur = conn.cursor()
    cur.execute(
        "select table_schema, table_name, column_name, data_type "
        "from iceberg.information_schema.columns "
        f"where table_schema in ({placeholders}) "
        "order by table_schema, table_name, ordinal_position",
        names,
    )
    out: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for schema, table, column, ctype in cur.fetchall():
        out.setdefault((schema, table), []).append((column, ctype))
    return out


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sample_values(
    conn: Any, relation: tuple[str, str, str], columns: Sequence[str]
) -> dict[str, list[str]]:
    """Up to SAMPLE_VALUES distinct non-null values per column from SAMPLE_ROWS rows."""
    if not columns:
        return {}
    cols = ", ".join(quote_ident(c) for c in columns)
    table = ".".join(quote_ident(p) for p in relation)
    cur = conn.cursor()
    cur.execute(f"select {cols} from {table} limit {SAMPLE_ROWS}")
    rows = cur.fetchall()
    samples: dict[str, list[str]] = {}
    for i, column in enumerate(columns):
        values: list[str] = []
        for row in rows:
            value = row[i]
            text = None if value is None else str(value)[:60]
            if text is not None and text not in values:
                values.append(text)
        samples[column] = values[:SAMPLE_VALUES]
    return samples


# --- Ollama ----------------------------------------------------------------------------------


def default_gateway(route_file: Path = Path("/proc/net/route")) -> str | None:
    """WSL NAT: the Windows host is the default gateway of eth0."""
    try:
        lines = route_file.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) > 2 and parts[1] == "00000000":
            raw = bytes.fromhex(parts[2])
            return ".".join(str(b) for b in reversed(raw))
    return None


def normalise_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if not re.match(r"^https?://", host):
        host = "http://" + host
    if not re.search(r":\d+$", host.split("://", 1)[1]):
        host = f"{host}:{OLLAMA_PORT}"
    return host


def ollama_candidates(env: dict[str, str] | None = None, gateway: str | None = None) -> list[str]:
    env = dict(os.environ) if env is None else env
    if env.get("OLLAMA_HOST"):
        return [normalise_host(env["OLLAMA_HOST"])]
    hosts = [f"http://localhost:{OLLAMA_PORT}"]
    if gateway:
        hosts.append(f"http://{gateway}:{OLLAMA_PORT}")
    return hosts


def find_ollama(model: str, candidates: Sequence[str], session: Any = requests) -> str:
    """The first candidate that answers and has the model; a clear error otherwise."""
    for base in candidates:
        try:
            session.get(f"{base}/api/version", timeout=3).raise_for_status()
        except requests.RequestException:
            continue
        tags = session.get(f"{base}/api/tags", timeout=10).json().get("models", [])
        names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
        if model not in names:
            raise DocsDraftError(
                f"Ollama at {base} does not have the model {model!r}: run "
                f"'ollama pull {model}' on Windows (available: {sorted(n for n in names if n)})."
            )
        return base
    raise DocsDraftError(
        "Ollama is not reachable (tried " + ", ".join(candidates) + "). Start Ollama on Windows. "
        "From WSL (NAT networking) it must listen on all interfaces: set OLLAMA_HOST=0.0.0.0 in "
        "the Windows environment and restart Ollama, or set OLLAMA_HOST here to its address."
    )


PROMPT = """You write dbt documentation for Wanderlake, a travel data lakehouse (cities, weather,
exchange rates, travel advice). Write short, factual descriptions in plain English: one sentence,
at most 20 words, ending with a full stop. Describe what the value means, using only the names,
SQL, types and sample values below; do not invent facts or mention samples.

{kind} {label} (Trino table {relation}).
Existing description: {description}
SQL:
{sql}

{task}
Answer with JSON only: {{"description": "...", "columns": {{"column_name": "...", ...}}}}
{description_rule}"""


def build_prompt(target: Target, columns: dict[str, str], samples: dict[str, list[str]]) -> str:
    label = f"{target.source_name}.{target.name}" if target.source_name else target.name
    lines = [
        f"- {c} ({ctype}); sample values: {', '.join(samples.get(c, [])) or 'none'}"
        for c, ctype in columns.items()
    ]
    task = "Describe these columns:\n" + "\n".join(lines) if lines else "No columns to describe."
    rule = (
        'Put the table description in "description".'
        if target.draft_node
        else 'Leave "description" empty: the table is already described.'
    )
    return PROMPT.format(
        kind=target.kind.capitalize(),
        label=label,
        relation=".".join(target.relation),
        description=target.description or "(none)",
        sql=target.sql or "(source table: no SQL)",
        task=task,
        description_rule=rule,
    )


def ask_ollama(base: str, model: str, prompt: str, session: Any = requests) -> dict[str, Any]:
    response = session.post(
        f"{base}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=600,
    )
    response.raise_for_status()
    try:
        answer = json.loads(response.json().get("response") or "{}")
    except json.JSONDecodeError:
        return {}
    return answer if isinstance(answer, dict) else {}


def clean(text: Any) -> str | None:
    """A usable one-line description, or None."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip().strip('"').strip()
    if "draft" in text.lower() or len(text.split()) < 3 or len(text) > MAX_DESCRIPTION_CHARS:
        return None
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


# --- writing the YAML ------------------------------------------------------------------------


def yaml_string(text: str) -> str:
    return json.dumps(MARKER + text, ensure_ascii=False)  # a JSON string is a YAML string


def load_yaml(text: str) -> Any:
    from ruamel.yaml import YAML

    return YAML().load(text)


def find_entry(doc: Any, target: Target) -> Any:
    if target.kind == "source":
        for source in doc.get("sources") or []:
            if source.get("name") == target.source_name:
                for table in source.get("tables") or []:
                    if table.get("name") == target.name:
                        return table
        return None
    for entry in doc.get(f"{target.kind}s") or []:
        if entry.get("name") == target.name:
            return entry
    return None


def block_end(lines: list[str], start: int, dash_col: int) -> int:
    """Index after the last line of the block sequence item that starts at `start`."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and len(line) - len(stripped) <= dash_col:
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def plan_edits(
    lines: list[str], entry: Any, node_text: str | None, columns: dict[str, str]
) -> list[tuple[int, int, list[str]]]:
    """Edits as (start, end, new_lines), replacing lines[start:end]."""
    edits: list[tuple[int, int, list[str]]] = []
    key_col = entry.lc.col
    first = entry.lc.line
    if node_text is not None and "description" not in entry:
        edits.append(
            (first + 1, first + 1, [" " * key_col + f"description: {yaml_string(node_text)}"])
        )
    elif node_text is not None and is_missing(entry.get("description")):
        line = entry.lc.key("description")[0]
        edits.append((line, line + 1, [" " * key_col + f"description: {yaml_string(node_text)}"]))

    existing = entry.get("columns")
    by_name = {str(c.get("name")).lower(): (i, c) for i, c in enumerate(existing or [])}
    new_items = []
    for column, text in columns.items():
        found = by_name.get(column.lower())
        if found is None:
            new_items.append((column, text))
            continue
        i, item = found
        if not is_missing(item.get("description")):
            continue  # never overwrite
        line_no, col = existing.lc.item(i)
        if item.fa.flow_style():
            line = lines[line_no]
            value = f"description: {yaml_string(text)}"  # bound now; no backslash expansion
            if "description" in item:  # an empty description in a flow mapping
                line = re.sub(r"description:\s*(''|\"\")?", lambda _m, v=value: v, line, count=1)
            else:
                line = re.sub(
                    r"(name:\s*[^,}]+)", lambda m, v=value: f"{m.group(1)}, {v}", line, count=1
                )
            edits.append((line_no, line_no + 1, [line]))
        elif "description" in item:
            d_line = item.lc.key("description")[0]
            edits.append((d_line, d_line + 1, [" " * col + f"description: {yaml_string(text)}"]))
        else:
            n_line = item.lc.key("name")[0]
            edits.append(
                (n_line + 1, n_line + 1, [" " * col + f"description: {yaml_string(text)}"])
            )

    if new_items:
        if existing:
            last = len(existing) - 1
            line_no, col = existing.lc.item(last)
            dash_col = col - 2
            at = block_end(lines, line_no, dash_col)
            flow = existing[last].fa.flow_style()
            prefix = []
        else:
            dash_col = key_col + 2
            at = block_end(lines, first, key_col - 2)
            flow = False
            prefix = [" " * key_col + "columns:"]
        out = list(prefix)
        for column, text in new_items:
            if flow:
                out.append(
                    " " * dash_col + f"- {{name: {column}, description: {yaml_string(text)}}}"
                )
            else:
                out.append(" " * dash_col + f"- name: {column}")
                out.append(" " * (dash_col + 2) + f"description: {yaml_string(text)}")
        edits.append((at, at, out))
    return edits


def apply_edits(lines: list[str], edits: list[tuple[int, int, list[str]]]) -> list[str]:
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[start:end] = new
    return lines


def descriptions(doc: Any) -> list[str]:
    """Every non-empty description in a YAML document, for the never-overwrite check."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str) and value.strip():
                    found.append(value.strip())
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return found


def write_drafts(path: Path, drafts: list[tuple[Target, str | None, dict[str, str]]]) -> None:
    """Write the drafts for one YAML file, then check that every existing description survived."""
    original = path.read_text()
    lines = original.split("\n")
    doc = load_yaml(original)
    edits = []
    for target, node_text, columns in drafts:
        entry = find_entry(doc, target)
        if entry is None:
            raise DocsDraftError(f"{path}: no entry for {target.name}")
        edits += plan_edits(lines, entry, node_text, columns)
    updated = "\n".join(apply_edits(lines, edits))
    before, after = descriptions(doc), descriptions(load_yaml(updated))
    missing = [d for d in before if d not in after]
    if missing:
        raise DocsDraftError(
            f"{path}: refusing to write, existing descriptions changed: {missing[:3]}"
        )
    path.write_text(updated)


# --- main ------------------------------------------------------------------------------------


def dbt_parse() -> dict[str, Any]:
    dbt = ROOT / ".venv-dbt/bin/dbt"
    if not dbt.exists():
        raise DocsDraftError(f"{dbt} not found: run 'make dbt-venv' first.")
    env = {**os.environ, "DBT_PROFILES_DIR": str(DBT_DIR), "DBT_TARGET": "local"}
    subprocess.run([str(dbt), "parse", "--quiet"], cwd=DBT_DIR, env=env, check=True)
    return json.loads((DBT_DIR / "target/manifest.json").read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--select", action="append", default=[], help="model/seed/table name")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--dry-run", action="store_true", help="print drafts, write nothing")
    parser.add_argument("--trino-host", default="localhost")
    parser.add_argument("--trino-port", type=int, default=int(os.environ.get("TRINO_PORT", "8081")))
    args = parser.parse_args(argv)

    try:
        base = find_ollama(args.model, ollama_candidates(gateway=default_gateway()))
        print(f"Ollama: {base}, model {args.model}")
        manifest = dbt_parse()
        try:
            conn = trino_connect(args.trino_host, args.trino_port)
            schemas = {
                n["schema"]
                for n in list(manifest["nodes"].values()) + list(manifest["sources"].values())
            }
            columns = read_trino_columns(conn, schemas)
        except Exception as exc:
            raise DocsDraftError(
                f"Trino on {args.trino_host}:{args.trino_port}: {exc}; run 'make mode-query'."
            ) from exc
        targets, short = find_targets(manifest, columns, args.select)

        by_file: dict[Path, list[tuple[Target, str | None, dict[str, str]]]] = {}
        drafted_nodes = drafted_columns = skipped = 0
        examples: list[str] = []
        for target in targets:
            samples = sample_values(conn, target.relation, list(target.columns))
            names = list(target.columns)
            chunks = [
                names[i : i + COLUMNS_PER_REQUEST]
                for i in range(0, len(names), COLUMNS_PER_REQUEST)
            ] or [[]]
            node_text, column_texts = None, {}
            for n, chunk in enumerate(chunks):
                ask_node = target.draft_node and n == 0
                prompt_target = (
                    target if ask_node else Target(**{**target.__dict__, "draft_node": False})
                )
                answer = ask_ollama(
                    base,
                    args.model,
                    build_prompt(prompt_target, {c: target.columns[c] for c in chunk}, samples),
                )
                if ask_node:
                    node_text = clean(answer.get("description"))
                got = answer.get("columns") if isinstance(answer.get("columns"), dict) else {}
                lower = {str(k).lower(): v for k, v in got.items()}
                for column in chunk:
                    text = clean(lower.get(column.lower()))
                    if text:
                        column_texts[column] = text
                    else:
                        skipped += 1
            drafted_nodes += node_text is not None
            drafted_columns += len(column_texts)
            label = f"{target.source_name}.{target.name}" if target.source_name else target.name
            print(
                f"{label}: {len(column_texts)}/{len(target.columns)} columns"
                + (", table" if node_text else "")
            )
            for column, text in column_texts.items():
                examples.append(f"{label}.{column}: {MARKER}{text}")
            if node_text or column_texts:
                by_file.setdefault(target.yaml_path, []).append((target, node_text, column_texts))

        if not args.dry_run:
            for path, drafts in by_file.items():
                write_drafts(path, drafts)
        print(
            f"\nDrafted {drafted_nodes} table and {drafted_columns} column descriptions"
            f" ({skipped} answers unusable, skipped) in {len(by_file)} files"
            + (" (dry run: nothing written)." if args.dry_run else ".")
        )
        if short:
            print(f"\nShort descriptions left as they are ({len(short)}; review by hand):")
            print("\n".join(f"  {s}" for s in short))
        if examples:
            print("\nExamples:\n" + "\n".join(f"  {e}" for e in examples[:5]))
        return 0
    except DocsDraftError as exc:
        print(f"docs-draft: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
