from __future__ import annotations

import pytest

from ingestion.common.http import DEFAULT_TIMEOUT, build_session
from ingestion.common.logging import format_event
from ingestion.common.s3 import S3ConfigError, put_json, s3_client


def test_s3_client_requires_credentials() -> None:
    with pytest.raises(S3ConfigError, match="S3_ACCESS_KEY, S3_SECRET_KEY"):
        s3_client(env={})


def test_s3_client_uses_generic_env_and_path_style() -> None:
    client = s3_client(
        env={
            "S3_ENDPOINT": "http://seaweedfs:8333",
            "S3_ACCESS_KEY": "a",
            "S3_SECRET_KEY": "b",
            "S3_REGION": "auto",
        }
    )
    assert client.meta.endpoint_url == "http://seaweedfs:8333"
    assert client.meta.region_name == "auto"
    assert client.meta.config.s3["addressing_style"] == "path"


def test_put_json_writes_bytes_and_metadata(s3) -> None:
    body = b'[{"date":"2026-11-17"}]'
    put_json("raw", "x/y.json", body, metadata={"http_status": 200})

    obj = s3.get_object(Bucket="raw", Key="x/y.json")
    assert obj["Body"].read() == body
    assert obj["Metadata"] == {"http_status": "200"}


def test_session_has_default_timeout_and_retries() -> None:
    session = build_session(total_retries=4)
    assert session.timeout == DEFAULT_TIMEOUT
    retry = session.get_adapter("https://example.com").max_retries
    assert retry.total == 4
    assert 429 in retry.status_forcelist and 503 in retry.status_forcelist


def test_format_event_quotes_values_with_spaces() -> None:
    line = format_event("x.done", key="a/b.json", reason="not yet published", rows=3)
    assert line == 'x.done key=a/b.json reason="not yet published" rows=3'
