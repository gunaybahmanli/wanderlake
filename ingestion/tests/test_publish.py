"""Cloud publish (ingestion.publish), offline: moto (R2), SQLite Iceberg catalog, fake Postgres."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import boto3
import pyarrow as pa
import pytest
import yaml
from moto import mock_aws
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.common.s3 import S3ConfigError, s3_client
from ingestion.publish import job, migrations, r2, supabase
from ingestion.publish.tables import TABLE_NAMES, get_table

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
GOLD_YML = ROOT / "dbt" / "models" / "gold" / "_gold.yml"
BUCKET = "wanderlake-lake"

# dbt contract type (Trino) -> Postgres type in the api migrations
TYPE_MAP = {
    "varchar": "text",
    "double": "double precision",
    "bigint": "bigint",
    "integer": "integer",
    "boolean": "boolean",
    "date": "date",
    "timestamp(6) with time zone": "timestamptz",
    "decimal(28, 12)": "numeric(28, 12)",
    "array(varchar)": "text[]",
}


# --- tables -------------------------------------------------------------------------------


def test_only_exchange_rates_are_limited_to_400_days() -> None:
    snapshot = date(2026, 11, 22)
    assert get_table("fct_exchange_rate").cutoff(snapshot) == date(2025, 10, 18)
    for name in TABLE_NAMES:
        if name != "fct_exchange_rate":
            assert get_table(name).cutoff(snapshot) is None


def test_unknown_table_is_an_error() -> None:
    with pytest.raises(KeyError, match="not a published table"):
        get_table("stg_cbar_rates")


# --- migrations ---------------------------------------------------------------------------


def api_tables_in_migrations() -> dict[str, dict[str, str]]:
    """{table: {column: type}} from the create table statements of the migrations."""
    tables: dict[str, dict[str, str]] = {}
    for m in migrations.discover(MIGRATIONS_DIR):
        for name, body in re.findall(
            r"create table if not exists api\.(\w+) \((.*?)\n\);", m.sql, re.S
        ):
            columns = {}
            for line in body.strip().splitlines():
                line = line.strip().rstrip(",")
                if not line or line.startswith(("primary key", "--")):
                    continue
                col, rest = line.split(" ", 1)
                rest = re.sub(r" (primary key|not null)", "", rest)
                columns[col] = rest
            tables[name] = columns
    return tables


def gold_contracts() -> dict[str, dict[str, str]]:
    models = yaml.safe_load(GOLD_YML.read_text())["models"]
    return {m["name"]: {c["name"]: c["data_type"] for c in m["columns"]} for m in models}


def test_migrations_create_every_published_table_like_its_dbt_contract() -> None:
    api, gold = api_tables_in_migrations(), gold_contracts()
    assert sorted(api) == sorted(TABLE_NAMES)
    for table, columns in api.items():
        expected = {col: TYPE_MAP[dbt_type] for col, dbt_type in gold[table].items()}
        assert columns == expected, f"api.{table} differs from the dbt contract of gold.{table}"


def test_rls_migration_covers_every_published_table() -> None:
    sql = (MIGRATIONS_DIR / "20261122100100_api_read_only_access.sql").read_text()
    listed = re.findall(r"'(\w+)'", sql.split("array[", 1)[1].split("]", 1)[0])
    assert sorted(listed) == sorted(TABLE_NAMES)
    assert "for select to anon using (true)" in sql
    assert "grant select on table api.%I to anon" in sql
    assert not re.search(r"grant (insert|update|delete|all)", sql)


def test_migrations_are_found_in_version_order() -> None:
    found = migrations.discover(MIGRATIONS_DIR)
    assert [m.version for m in found] == sorted(m.version for m in found)
    assert found[0].name == "api_schema"


def test_bad_migration_file_name_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "001_init.sql").write_text("select 1;")
    with pytest.raises(migrations.MigrationError, match="bad migration file name"):
        migrations.discover(tmp_path)


def test_pending_skips_applied_and_rejects_edited_migrations() -> None:
    first = migrations.Migration("20260101000000", "one", "select 1;")
    second = migrations.Migration("20260102000000", "two", "select 2;")
    assert migrations.pending([first, second], {first.version: first.checksum}) == [second]
    with pytest.raises(migrations.MigrationError, match="changed after it was applied"):
        migrations.pending([first], {first.version: "0" * 64})


# --- R2 -----------------------------------------------------------------------------------


@pytest.fixture
def cloud_s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("CLOUD_S3_ENDPOINT", raising=False)
    monkeypatch.setenv("CLOUD_S3_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("CLOUD_S3_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("CLOUD_S3_REGION", "us-east-1")
    monkeypatch.setenv("CLOUD_S3_BUCKET", BUCKET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield r2.client()


def test_cloud_prefix_reports_its_own_missing_variables() -> None:
    with pytest.raises(S3ConfigError, match="CLOUD_S3_ACCESS_KEY, CLOUD_S3_SECRET_KEY"):
        s3_client({}, prefix="CLOUD_S3_")


def test_r2_writes_snapshot_and_latest_parquet(cloud_s3: Any) -> None:
    table = pa.table({"city_id": ["baku", "lisbon"], "tags": [["a"], []]})
    keys = r2.write_table("dim_city", table, date(2026, 11, 22), cloud_s3, r2.bucket())
    assert keys == [
        "gold/dim_city/snapshot_date=2026-11-22/data.parquet",
        "gold/dim_city/latest/data.parquet",
    ]
    for key in keys:
        assert r2.parquet_rows(key, cloud_s3, BUCKET) == 2


# --- Supabase (fake connection) -----------------------------------------------------------


class FakeCopy:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def __enter__(self) -> FakeCopy:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def write_row(self, row: Any) -> None:
        self.rows.append(tuple(row))


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.rowcount = -1

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def execute(self, query: Any, params: Any = None) -> None:
        text = query if isinstance(query, str) else query.as_string()
        self.conn.statements.append(text)
        if text.startswith("insert into"):
            self.rowcount = len(self.conn.copied)

    def copy(self, query: Any) -> FakeCopy:
        self.conn.statements.append(query.as_string())
        return FakeCopy(self.conn.copied)

    def fetchone(self) -> tuple:
        return (len(self.conn.copied),)


class FakeConn:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.copied: list[tuple] = []
        self.transactions = 0

    def transaction(self) -> FakeConn:
        self.transactions += 1
        return self

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def test_replace_table_stages_with_copy_then_swaps_in_one_transaction() -> None:
    conn = FakeConn()
    rows = [("baku", ["Azerbaijani"]), ("lisbon", ["Portuguese"])]
    assert supabase.replace_table(conn, "dim_city", ["city_id", "language_names"], rows) == 2
    assert conn.transactions == 1
    assert conn.copied == rows
    steps = [s.split(" (")[0].split(" from")[0] for s in conn.statements]
    assert steps == [
        "select pg_advisory_xact_lock(hashtext(%s))",
        'create temporary table "staging_dim_city"',
        'copy "staging_dim_city"',
        "select count(*)",
        "delete",
        'insert into "api"."dim_city"',
    ]


def test_column_mismatch_is_an_error() -> None:
    supabase.check_columns("dim_city", ["a", "b"], ["b", "a"])  # order does not matter
    with pytest.raises(supabase.PublishError, match="does not exist"):
        supabase.check_columns("dim_city", [], ["a"])
    with pytest.raises(supabase.PublishError, match=r"missing in gold \['b'\], not in api \['c'\]"):
        supabase.check_columns("dim_city", ["a", "b"], ["a", "c"])


def test_missing_supabase_settings_are_named() -> None:
    with pytest.raises(supabase.SupabaseConfigError, match="SUPABASE_DB_PASSWORD"):
        supabase.connect({"SUPABASE_DB_HOST": "h", "SUPABASE_DB_USER": "u"})


# --- job ----------------------------------------------------------------------------------


@pytest.fixture
def gold_catalog(tmp_path: Path) -> SqlCatalog:
    catalog = SqlCatalog(
        "test",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
        init_catalog_tables="true",
    )
    catalog.create_namespace("gold")
    rates = pa.table(
        {
            "date": pa.array([date(2025, 1, 1), date(2026, 11, 1), date(2026, 11, 22)]),
            "rate": pa.array([1.0, 2.0, 3.0]),
        }
    )
    catalog.create_table("gold.fct_exchange_rate", schema=rates.schema).append(rates)
    cities = pa.table({"city_id": ["baku", "lisbon"]})
    catalog.create_table("gold.dim_city", schema=cities.schema).append(cities)
    return catalog


def test_read_gold_filters_exchange_rates_and_cross_checks_metadata(
    gold_catalog: SqlCatalog,
) -> None:
    snapshot = date(2026, 11, 22)
    rates = job.read_gold(get_table("fct_exchange_rate"), snapshot, gold_catalog)
    assert rates.num_rows == 2  # 2025-01-01 is older than 400 days
    assert job.read_gold(get_table("dim_city"), snapshot, gold_catalog).num_rows == 2


def test_rows_follow_the_target_column_order() -> None:
    table = pa.table({"b": [1, 2], "a": [["x"], []]})
    assert list(job.rows_in_order(table, ["a", "b"])) == [(["x"], 1), ([], 2)]


def counts(table: str, *values: int) -> job.TableCounts:
    return job.TableCounts(table, *values)


def test_verification_passes_only_when_every_count_matches() -> None:
    job.check_counts([counts("dim_city", 10, 10, 10, 10)], ["dim_city"])
    with pytest.raises(job.VerificationError, match=r"row counts differ for \['dim_city'\]"):
        job.check_counts([counts("dim_city", 10, 9, 10, 10)], ["dim_city"])
    with pytest.raises(job.VerificationError, match=r"tables not published: \['dim_country'\]"):
        job.check_counts([counts("dim_city", 10, 10, 10, 10)], ["dim_city", "dim_country"])


def test_counts_table_marks_mismatches() -> None:
    text = job.format_counts([counts("dim_city", 10, 10, 10, 10), counts("x", 1, 0, 1, 1)])
    lines = text.splitlines()
    assert lines[0].split() == ["table", "source", "supabase", "r2_snapshot", "r2_latest", "ok"]
    assert lines[1].split()[-1] == "yes" and lines[2].split()[-1] == "NO"
