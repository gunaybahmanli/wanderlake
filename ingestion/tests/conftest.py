from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import boto3
import pytest
from moto import mock_aws

BUCKET = "raw"


def ecb_rows(rate_date: str, quotes: dict[str, float] | None = None) -> list[dict[str, Any]]:
    quotes = quotes or {"USD": 1.0842, "GBP": 0.83559, "JPY": 164.12}
    return [{"date": rate_date, "base": "EUR", "quote": q, "rate": r} for q, r in quotes.items()]


def ecb_body(rate_date: str) -> bytes:
    # Compact separators, like the real API, so byte-for-byte checks are meaningful.
    return json.dumps(ecb_rows(rate_date), separators=(",", ":")).encode()


@pytest.fixture
def s3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic S3_* variables for moto (no endpoint, so calls go to the mocked AWS)."""
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    monkeypatch.setenv("S3_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("S3_REGION", "us-east-1")


@pytest.fixture
def s3(s3_env: None) -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


class ScriptedServer:
    """Local HTTP server that replays a scripted list of (status, body) responses.

    Used for retry tests: unlike requests-mock, a real socket exercises urllib3's Retry.
    """

    def __init__(self) -> None:
        self.script: list[tuple[int, bytes]] = []
        self.calls = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
                server.calls += 1
                status, body = server.script[min(server.calls, len(server.script)) - 1]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)


@pytest.fixture
def http_server() -> Iterator[ScriptedServer]:
    server = ScriptedServer()
    server.thread.start()
    yield server
    server.httpd.shutdown()
    server.httpd.server_close()
