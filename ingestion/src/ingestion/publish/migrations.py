"""Versioned SQL migrations for Supabase (supabase/migrations/<version>_<name>.sql).

Files are applied in version order, each once, inside one transaction holding an advisory lock;
wl_meta.schema_migrations (a private schema the REST API does not expose) records the version
and a SHA-256 checksum. Editing a file that was already applied is an error: add a new migration.
The SQL itself is idempotent too, so a lost history table only means the files run again.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.common.logging import get_logger, log_event

log = get_logger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parents[4] / "supabase" / "migrations"
FILE_PATTERN = re.compile(r"^(\d{14})_([a-z0-9_]+)\.sql$")
LOCK_KEY = 4_242_001  # pg_advisory_xact_lock key for migrations

BOOTSTRAP_SQL = """
create schema if not exists wl_meta;
revoke all on schema wl_meta from public;
create table if not exists wl_meta.schema_migrations (
  version text primary key,
  name text not null,
  checksum text not null,
  applied_at timestamptz not null default now()
);
"""


class MigrationError(RuntimeError):
    """Raised for a malformed migrations folder or an edited, already-applied migration."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def migrations_dir() -> Path:
    return Path(os.environ.get("SUPABASE_MIGRATIONS_DIR") or DEFAULT_DIR)


def discover(directory: Path | None = None) -> list[Migration]:
    """All migrations in version order. Any other .sql file name is an error."""
    directory = directory or migrations_dir()
    found = []
    for path in sorted(directory.glob("*.sql")):
        match = FILE_PATTERN.match(path.name)
        if not match:
            raise MigrationError(
                f"bad migration file name {path.name!r}: want <14 digits>_name.sql"
            )
        found.append(Migration(match[1], match[2], path.read_text()))
    if not found:
        raise MigrationError(f"no migrations in {directory}")
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration versions in {directory}")
    return found


def pending(migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    """Migrations not applied yet; fails if an applied one's checksum changed."""
    for m in migrations:
        if m.version in applied and applied[m.version] != m.checksum:
            raise MigrationError(
                f"migration {m.version}_{m.name} was changed after it was applied; "
                "revert the edit and add a new migration instead"
            )
    return [m for m in migrations if m.version not in applied]


def apply(conn: Any, directory: Path | None = None) -> list[str]:
    """Apply pending migrations in one transaction. Returns the versions applied."""
    migrations = discover(directory)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        cur.execute(BOOTSTRAP_SQL)
        cur.execute("select version, checksum from wl_meta.schema_migrations")
        applied = dict(cur.fetchall())
        todo = pending(migrations, applied)
        for m in todo:
            cur.execute(m.sql)
            cur.execute(
                "insert into wl_meta.schema_migrations (version, name, checksum) "
                "values (%s, %s, %s)",
                (m.version, m.name, m.checksum),
            )
            log_event(log, "supabase.migration_applied", version=m.version, name=m.name)
    log_event(log, "supabase.migrations", applied=len(todo), total=len(migrations))
    return [m.version for m in todo]
