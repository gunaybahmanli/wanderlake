"""Environment of a run: the same code runs on the laptop (dev) and in GitHub Actions (prod).

Environments differ only by configuration (ADR-0012):
    WANDERLAKE_ENV         dev (default) or prod
    WANDERLAKE_RAW_BUCKET  bucket for raw source responses; default "raw" (SeaweedFS locally,
                           wanderlake-raw on R2 in prod)
"""

from __future__ import annotations

import os
from collections.abc import Mapping

ENVIRONMENTS = ("dev", "prod")
DEFAULT_RAW_BUCKET = "raw"


class WanderlakeEnvError(RuntimeError):
    """Raised when WANDERLAKE_ENV has an unknown value."""


def wanderlake_env(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = (env.get("WANDERLAKE_ENV") or "dev").strip().lower()
    if value not in ENVIRONMENTS:
        raise WanderlakeEnvError(f"WANDERLAKE_ENV must be one of {ENVIRONMENTS}, not {value!r}")
    return value


def raw_bucket(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("WANDERLAKE_RAW_BUCKET") or DEFAULT_RAW_BUCKET
