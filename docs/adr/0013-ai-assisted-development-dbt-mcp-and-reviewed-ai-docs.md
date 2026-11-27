# ADR-0013: AI-assisted development: dbt MCP (read-only) and human-reviewed AI documentation

- **Status:** Proposed
- **Date:** 2026-11-27

## Context

Two jobs on the dbt project are slow by hand and well suited to AI help:

- **Understanding the project while changing it.** An AI coding assistant (Claude Code) gives
  better answers when it can query the project itself (lineage, columns, compiled SQL, a few
  rows) instead of guessing from files. The open-source dbt MCP server from dbt Labs exposes
  exactly that through the Model Context Protocol, but by default it also exposes `build`, `run`,
  `test` and other tools that change data.
- **Documentation gaps.** Every model and source is described, but about 200 columns, mostly in
  staging and bronze, are not in the YAML at all. Writing them is tedious; a language model can
  draft them, but it can also invent plausible-sounding facts.

Constraints: no paid services, no data leaving the laptop for documentation work, no secrets in
the repository, and nothing that can touch production (ADR-0012: only the prod pipeline writes to
production).

## Decision

**1. dbt MCP server, read-only, dev only.** The project-scoped `.mcp.json` starts
`scripts/dbt-mcp.sh`, which runs `dbt-mcp` (pinned) against the local dbt project and dbt
(`.venv-dbt`, same pins as CI and prod). Read-only is enforced twice:

- **Tools:** `DBT_MCP_ENABLE_TOOLS` is an allowlist that overrides every other tool setting, so
  only `list`, `parse`, `compile`, `show`, `get_lineage_dev` and `get_node_details_dev` exist.
  `DISABLE_TOOLS` repeats the ones that run or change data, and every dbt platform toolset
  (Semantic Layer, Discovery, Admin API, SQL, codegen, LSP) is off. `make mcp-check` starts the
  server and fails if any other tool appears; a unit test checks the allowlist.
- **Database:** dbt uses target `local` (the laptop's Trino, never prod) as the Trino user
  `wanderlake_mcp`, which the dev Trino's file-based access control allows only to read. This is
  needed because `show` executes the SQL it is given: it adds a row limit (5 by default), but SQL
  that contains its own `LIMIT` runs unwrapped.

**2. AI-drafted documentation, always reviewed by a human.** `make docs-draft` finds empty or
missing descriptions (models, seeds, source tables, columns, including columns not yet in the
YAML) and asks a **local** Ollama model (`llama3.2:3b` by default) for one sentence from the name,
the model SQL, the column type and up to 3 sample values (read as `wanderlake_mcp` with
`LIMIT 5`).

- Every draft is written with the marker `(draft) `. A human reviews it, corrects it and removes
  the marker; only then is it documentation.
- Existing non-empty descriptions are never changed (very short ones are only listed for a
  human). The YAML is edited line by line, so every other byte of the file stays the same, and
  the script refuses to write if any existing description would change.
- **Guard:** `make docs-check` and a CI job fail while any marker remains in dbt's YAML. A pull
  request with drafts is red until they are reviewed, so unreviewed AI text cannot reach `main`
  unnoticed.

Ollama runs on Windows; WSL (NAT networking) reaches it at the Windows host, which is WSL's
default gateway. The script finds it (`OLLAMA_HOST`, then `localhost`, then the gateway) and stops
with instructions if it is not running.

## Alternatives considered

- **dbt MCP with its defaults.** Everything enabled, including `build` and `run`: an assistant
  could rebuild tables while exploring. Rejected: inspection is the goal.
- **Tool allowlist only.** Simpler, but `show` would still pass arbitrary SQL to Trino. The
  read-only Trino user closes that gap at the database.
- **dbt platform (Cloud) MCP features** (Discovery API, Semantic Layer, remote SQL). Need a dbt
  platform account and send metadata to it; the local CLI tools cover what we need.
- **A hosted LLM for drafts** (larger, better text). Sends model SQL and sample rows to a third
  party and costs money. A small local model is good enough for one-sentence drafts that a
  human reviews anyway.
- **dbt-codegen's `generate_model_yaml`.** Fills in column names, not descriptions.
- **Accepting AI text without a marker, reviewed in the pull request diff.** Easy to skim past
  200 lines; the marker and the CI check make the review explicit and impossible to forget.

## Consequences

- Claude Code can answer lineage and column questions from the project itself; it cannot build,
  run or write anything, and it only sees dev data.
- The dev Trino now has access control: `wanderlake_mcp` reads, every other user is unchanged.
  Without authentication the user name is only what the client claims, so this guards against
  accidents by tooling, not against a person on the laptop. Prod Trino is unaffected.
- Draft quality is that of a 3B model: plausible, sometimes wrong (units, meanings inferred from
  names). Review is real work, not a formality; the CI check enforces that it happens.
- A pull request with drafts shows a failing CI job until it is reviewed. That is the intent.
- `dbt-mcp` is pinned; upgrading it means checking the tool names (`make mcp-check`), since new
  versions can add tools.
- New dev dependencies: `ruamel.yaml` (locating YAML entries) and `trino` (samples).
