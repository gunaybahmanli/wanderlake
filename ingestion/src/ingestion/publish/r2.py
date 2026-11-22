"""Gold tables as Parquet on Cloudflare R2 (S3 API, CLOUD_S3_* variables).

Layout in the bucket (CLOUD_S3_BUCKET):
    gold/<table>/snapshot_date=YYYY-MM-DD/data.parquet   one file per table and publish day
    gold/<table>/latest/data.parquet                     the most recent publish (stable URL)
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping
from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ingestion.common.s3 import S3ConfigError, put_object, s3_client

PREFIX = "CLOUD_S3_"
PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"


def bucket(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    if not env.get("CLOUD_S3_BUCKET"):
        raise S3ConfigError("missing environment variables: CLOUD_S3_BUCKET")
    return env["CLOUD_S3_BUCKET"]


def client(env: Mapping[str, str] | None = None) -> Any:
    return s3_client(env, prefix=PREFIX)


def snapshot_key(table: str, snapshot_date: date) -> str:
    return f"gold/{table}/snapshot_date={snapshot_date.isoformat()}/data.parquet"


def latest_key(table: str) -> str:
    return f"gold/{table}/latest/data.parquet"


def to_parquet(arrow_table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(arrow_table, buffer, compression="zstd")
    return buffer.getvalue()


def write_table(
    table: str, arrow_table: pa.Table, snapshot_date: date, s3: Any, bucket_name: str
) -> list[str]:
    """Write the snapshot file, then the latest file. Returns both keys."""
    body = to_parquet(arrow_table)
    metadata = {"rows": str(arrow_table.num_rows), "snapshot_date": snapshot_date.isoformat()}
    keys = [snapshot_key(table, snapshot_date), latest_key(table)]
    for key in keys:
        put_object(bucket_name, key, body, PARQUET_CONTENT_TYPE, metadata=metadata, client=s3)
    return keys


def parquet_rows(key: str, s3: Any, bucket_name: str) -> int:
    """Row count of a Parquet object, read back from the bucket (footer metadata)."""
    body = s3.get_object(Bucket=bucket_name, Key=key)["Body"].read()
    return pq.read_metadata(io.BytesIO(body)).num_rows
