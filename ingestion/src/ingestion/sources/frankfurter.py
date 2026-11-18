"""Frankfurter API: daily ECB reference exchange rates (base EUR).

Endpoint (Frankfurter v2; v1 is deprecated):
    GET https://api.frankfurter.dev/v2/providers/ecb/rates?date=YYYY-MM-DD&base=EUR

The response is a JSON list of rows: {"date", "base", "quote", "rate"}.
On weekends, holidays, or before the ECB publishes (~16:00 CET), the API returns the
last available business day, so the returned rate date can be earlier than the requested one.
A date with no data at all (e.g. in the future) returns an empty list, which we treat as an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import requests

from ingestion.common.http import build_session
from ingestion.common.logging import get_logger, log_event
from ingestion.common.s3 import put_json

log = get_logger(__name__)

BASE_URL = "https://api.frankfurter.dev/v2"
RATES_PATH = "/providers/ecb/rates"
BASE_CURRENCY = "EUR"
KEY_TEMPLATE = "frankfurter/rates/date={date}/rates.json"


class FrankfurterError(RuntimeError):
    """Raised when the API returns an unusable response."""


@dataclass(frozen=True)
class RatesResponse:
    body: bytes  # exactly as received
    source_url: str
    http_status: int
    requested_date: date
    rate_date: date
    rows: int


def build_key(requested_date: date) -> str:
    """Object key for a requested (logical) date. Same date -> same key (idempotent)."""
    return KEY_TEMPLATE.format(date=requested_date.isoformat())


def fetch_rates(
    requested_date: date,
    session: requests.Session | None = None,
    base_url: str = BASE_URL,
) -> RatesResponse:
    """Fetch ECB rates for a date. Returns the raw body plus what we learned from it."""
    session = session or build_session()
    params = {"date": requested_date.isoformat(), "base": BASE_CURRENCY}

    response = session.get(base_url + RATES_PATH, params=params)
    response.raise_for_status()

    try:
        payload: Any = json.loads(response.content)
    except ValueError as exc:
        raise FrankfurterError(f"response is not valid JSON: {response.url}") from exc

    if not isinstance(payload, list):
        raise FrankfurterError(f"expected a JSON list, got {type(payload).__name__}")
    if not payload:
        raise FrankfurterError(f"no rates available for {requested_date.isoformat()}")

    rate_dates = {row["date"] for row in payload}
    if len(rate_dates) != 1:
        raise FrankfurterError(f"expected one rate date, got {sorted(rate_dates)}")
    rate_date = date.fromisoformat(rate_dates.pop())

    result = RatesResponse(
        body=response.content,
        source_url=response.url,
        http_status=response.status_code,
        requested_date=requested_date,
        rate_date=rate_date,
        rows=len(payload),
    )
    log_event(
        log,
        "frankfurter.fetched",
        requested_date=requested_date.isoformat(),
        rate_date=rate_date.isoformat(),
        rows=result.rows,
        status=result.http_status,
        bytes=len(result.body),
    )
    if rate_date != requested_date:
        log_event(
            log,
            "frankfurter.rate_date_differs",
            requested_date=requested_date.isoformat(),
            rate_date=rate_date.isoformat(),
            reason="weekend, holiday, or not yet published",
        )
    return result


def ingest_rates(
    requested_date: date,
    bucket: str,
    session: requests.Session | None = None,
    s3: Any | None = None,
) -> dict[str, str]:
    """Fetch rates for a date and store the raw response in the bronze bucket."""
    result = fetch_rates(requested_date, session=session)
    key = build_key(requested_date)
    metadata = {
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": result.source_url,
        "http_status": str(result.http_status),
        "requested_date": result.requested_date.isoformat(),
        "rate_date": result.rate_date.isoformat(),
    }
    put_json(bucket, key, result.body, metadata=metadata, client=s3)
    log_event(log, "frankfurter.ingested", bucket=bucket, key=key, **metadata)
    return {"bucket": bucket, "key": key, **metadata}
