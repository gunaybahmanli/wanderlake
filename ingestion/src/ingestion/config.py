"""Typed, validated access to config/cities.yaml (the single source of truth for cities).

Usage:
    from ingestion.config import load_cities
    for city in load_cities():
        print(city.id, city.currency, city.rate_source)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

# config.py -> ingestion -> src -> ingestion/ -> repo root. In the Airflow container the package
# lives at /opt/wanderlake/ingestion/src and the config folder is mounted at /opt/wanderlake/config.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CITIES_FILE = REPO_ROOT / "config" / "cities.yaml"
CITIES_FILE_ENV = "WANDERLAKE_CITIES_FILE"


def _matches(pattern: str, description: str):
    compiled = re.compile(pattern)

    def check(value: str) -> str:
        if not compiled.fullmatch(value):
            raise ValueError(f"must be {description}")
        return value

    return AfterValidator(check)


Slug = Annotated[
    str, _matches(r"[a-z0-9]+(-[a-z0-9]+)*", "a lowercase kebab-case slug, e.g. new-york-city")
]
CountryCode = Annotated[str, _matches(r"[A-Z]{2}", "2 uppercase letters (ISO 3166-1 alpha-2)")]
CurrencyCode = Annotated[str, _matches(r"[A-Z]{3}", "3 uppercase letters (ISO 4217)")]
WikidataId = Annotated[str, _matches(r"Q[1-9][0-9]*", "a Wikidata item id, e.g. Q220")]
# A Commons file name without the "File:" prefix, e.g. "Rome Skyline (8012016319).jpg".
CommonsFile = Annotated[
    str,
    _matches(
        r"(?i)(?!file:)[^/|#<>\[\]{}\n]+\.(jpe?g|png|webp|tiff?|gif)",
        'a Wikimedia Commons image file name without "File:" (e.g. Rome Skyline.jpg)',
    ),
]


class CityConfigError(ValueError):
    """Raised when the cities file is missing, unreadable or invalid."""


class City(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: Slug
    name: Annotated[str, Field(min_length=1)]
    country_code: CountryCode
    timezone: str
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    currency: CurrencyCode
    rate_source: Literal["ecb", "cbar"]
    inside_airbnb: bool | None
    geonames_id: Annotated[int, Field(gt=0)]
    wikidata_id: WikidataId
    cover_image_file: CommonsFile | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("must be a valid IANA time zone, e.g. Europe/Madrid") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class CityConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cities: Annotated[list[City], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_ids(self) -> CityConfig:
        ids = [c.id for c in self.cities]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate city id(s): {', '.join(duplicates)}")
        gids = [c.geonames_id for c in self.cities]
        dup_gids = sorted({g for g in gids if gids.count(g) > 1})
        if dup_gids:
            raise ValueError(f"duplicate geonames_id(s): {', '.join(map(str, dup_gids))}")
        qids = [c.wikidata_id for c in self.cities]
        dup_qids = sorted({q for q in qids if qids.count(q) > 1})
        if dup_qids:
            raise ValueError(f"duplicate wikidata_id(s): {', '.join(dup_qids)}")
        return self


def _format_errors(path: Path, raw: object, exc: ValidationError) -> str:
    raw_cities = raw.get("cities") if isinstance(raw, dict) else None
    lines = []
    for err in exc.errors():
        loc = list(err["loc"])
        where = ".".join(str(p) for p in loc) or "cities"
        if len(loc) >= 2 and loc[0] == "cities" and isinstance(loc[1], int):
            label = f"cities[{loc[1]}]"
            if isinstance(raw_cities, list) and loc[1] < len(raw_cities):
                entry = raw_cities[loc[1]]
                if isinstance(entry, dict) and entry.get("id"):
                    label += f" ({entry['id']})"
            where = ".".join([label, *(str(p) for p in loc[2:])])
        message = err["msg"].removeprefix("Value error, ")
        # Show the offending value for field errors; whole-file errors would dump everything.
        got = f" (got {err['input']!r})" if len(loc) > 2 else ""
        lines.append(f"  {where}: {message}{got}")
    return f"{path.name}: {len(lines)} validation error(s)\n" + "\n".join(lines)


def load_cities(path: str | Path | None = None) -> list[City]:
    """Load and validate the city list. Raises CityConfigError with a readable message."""
    if path is None:
        path = os.environ.get(CITIES_FILE_ENV) or DEFAULT_CITIES_FILE
    path = Path(path)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CityConfigError(f"cities file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise CityConfigError(f"{path.name}: invalid YAML: {exc}") from exc

    try:
        return list(CityConfig.model_validate(raw).cities)
    except ValidationError as exc:
        raise CityConfigError(_format_errors(path, raw, exc)) from exc
