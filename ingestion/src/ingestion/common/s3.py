"""S3 client built from the generic S3_* environment variables.

Works with SeaweedFS locally and Cloudflare R2 in the cloud:
    S3_ENDPOINT    e.g. http://seaweedfs:8333 (optional; unset = AWS default endpoint)
    S3_ACCESS_KEY  access key id
    S3_SECRET_KEY  secret access key
    S3_REGION      e.g. us-east-1 (R2 uses "auto"); defaults to us-east-1
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import boto3
from botocore.config import Config

from ingestion.common.logging import get_logger, log_event

log = get_logger(__name__)


class S3ConfigError(RuntimeError):
    """Raised when required S3_* environment variables are missing."""


def s3_client(env: Mapping[str, str] | None = None) -> Any:
    """Return a boto3 S3 client using path-style addressing."""
    env = os.environ if env is None else env
    missing = [name for name in ("S3_ACCESS_KEY", "S3_SECRET_KEY") if not env.get(name)]
    if missing:
        raise S3ConfigError(f"missing environment variables: {', '.join(missing)}")

    return boto3.client(
        "s3",
        endpoint_url=env.get("S3_ENDPOINT") or None,
        aws_access_key_id=env["S3_ACCESS_KEY"],
        aws_secret_access_key=env["S3_SECRET_KEY"],
        region_name=env.get("S3_REGION") or "us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


def put_json(
    bucket: str,
    key: str,
    body: bytes,
    metadata: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Write a JSON payload to S3 byte-for-byte (no re-serialization). Overwrites the key."""
    client = client or s3_client()
    response = client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        Metadata={k: str(v) for k, v in (metadata or {}).items()},
    )
    log_event(log, "s3.put", bucket=bucket, key=key, bytes=len(body), etag=response.get("ETag"))
    return response
