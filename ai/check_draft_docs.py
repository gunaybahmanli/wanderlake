"""Fail while any AI-drafted dbt description is unreviewed (ADR-0013).

    make docs-check        # also a CI job

make docs-draft writes drafts with the marker "(draft)". A human reviews each one, edits it if
needed and removes the marker; this check fails as long as any marker is left in dbt's YAML,
so unreviewed AI text cannot reach main unnoticed.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "(draft)"
SKIP_DIRS = {"dbt_packages", "target", "logs"}


def yaml_files(dbt_dir: Path) -> Iterable[Path]:
    for path in sorted(dbt_dir.rglob("*.yml")):
        if not SKIP_DIRS.intersection(path.relative_to(dbt_dir).parts):
            yield path


def find_markers(dbt_dir: Path) -> list[str]:
    """'path:line: text' for every line that contains the marker."""
    found = []
    for path in yaml_files(dbt_dir):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if MARKER in line:
                found.append(f"{path.relative_to(dbt_dir.parent)}:{number}: {line.strip()}")
    return found


def main(argv: list[str] | None = None) -> int:
    dbt_dir = Path(argv[0]) if argv else ROOT / "dbt"
    found = find_markers(dbt_dir)
    if found:
        print(f"{len(found)} unreviewed AI-drafted descriptions (remove '{MARKER}' after review):")
        print("\n".join(found))
        return 1
    print("docs-check: no unreviewed drafts.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
