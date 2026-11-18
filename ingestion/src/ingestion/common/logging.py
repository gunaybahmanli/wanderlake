"""Readable structured logging: an event name followed by key=value pairs.

Example:
    log = get_logger(__name__)
    log_event(log, "frankfurter.fetched", rate_date="2026-11-13", rows=30)
    # -> frankfurter.fetched rate_date=2026-11-13 rows=30

Uses the standard library, so Airflow captures these lines in its task logs.
"""

from __future__ import annotations

import logging
from typing import Any


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _format_value(value: Any) -> str:
    text = str(value)
    if text == "" or any(c.isspace() for c in text) or "=" in text or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def format_event(event: str, **fields: Any) -> str:
    parts = [event] + [f"{k}={_format_value(v)}" for k, v in fields.items()]
    return " ".join(parts)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, format_event(event, **fields))
