"""HTTP session with default timeouts and retries with exponential backoff."""

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ingestion import __version__

DEFAULT_TIMEOUT: tuple[float, float] = (5.0, 30.0)  # (connect, read) seconds
RETRY_STATUSES: tuple[int, ...] = (429, 500, 502, 503, 504)
USER_AGENT = f"wanderlake-ingestion/{__version__}"


class TimeoutSession(requests.Session):
    """A requests.Session that applies a default timeout to every request."""

    def __init__(self, timeout: tuple[float, float] = DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self.timeout = timeout

    def request(self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any):  # type: ignore[override]
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, *args, **kwargs)


def build_session(
    total_retries: int = 5,
    backoff_factor: float = 1.0,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> requests.Session:
    """Return a session that retries idempotent requests on 429/5xx and connection errors.

    Backoff between attempts is backoff_factor * 2 ** (attempt - 1) seconds (1s, 2s, 4s, ...),
    and a Retry-After header from the server is respected.
    """
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        respect_retry_after_header=True,
        raise_on_status=False,  # return the last response; callers use raise_for_status()
    )
    adapter = HTTPAdapter(max_retries=retry)

    session = TimeoutSession(timeout=timeout)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session
