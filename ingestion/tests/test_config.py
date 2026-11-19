from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ingestion.config import DEFAULT_CITIES_FILE, City, CityConfigError, load_cities

EXPECTED_IDS = [
    "baku",
    "madrid",
    "tokyo",
    "bangkok",
    "rome",
    "amsterdam",
    "dubai",
    "new-york-city",
    "prague",
    "seoul",
]

VALID_CITY = {
    "id": "rome",
    "name": "Rome",
    "country_code": "IT",
    "timezone": "Europe/Rome",
    "latitude": 41.8931,
    "longitude": 12.4828,
    "currency": "EUR",
    "rate_source": "ecb",
    "inside_airbnb": True,
    "geonames_id": 3169070,
}


def write_cities(tmp_path: Path, *cities: dict) -> Path:
    path = tmp_path / "cities.yaml"
    path.write_text(yaml.safe_dump({"cities": list(cities)}, sort_keys=False))
    return path


def city(**overrides) -> dict:
    return {**VALID_CITY, **overrides}


# --- the real file -------------------------------------------------------------


def test_real_file_loads_ten_cities() -> None:
    cities = load_cities()

    assert len(cities) == 10
    assert [c.id for c in cities] == EXPECTED_IDS
    assert all(isinstance(c, City) for c in cities)


def test_real_file_is_the_default_path() -> None:
    assert DEFAULT_CITIES_FILE.name == "cities.yaml"
    assert DEFAULT_CITIES_FILE.parent.name == "config"
    assert load_cities(DEFAULT_CITIES_FILE) == load_cities()


def test_real_file_rate_sources() -> None:
    by_id = {c.id: c for c in load_cities()}

    assert {i for i, c in by_id.items() if c.rate_source == "cbar"} == {"baku", "dubai"}
    assert by_id["baku"].currency == "AZN"
    assert by_id["new-york-city"].tz.key == "America/New_York"


def test_env_var_overrides_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WANDERLAKE_CITIES_FILE", str(write_cities(tmp_path, city())))

    assert [c.id for c in load_cities()] == ["rome"]


# --- bad entries ---------------------------------------------------------------


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(), city(name="Roma"))

    with pytest.raises(CityConfigError, match="duplicate city id\\(s\\): rome"):
        load_cities(path)


def test_bad_currency_is_rejected(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(currency="eur"))

    with pytest.raises(CityConfigError) as excinfo:
        load_cities(path)
    message = str(excinfo.value)
    assert "cities[0] (rome).currency" in message
    assert "3 uppercase letters (ISO 4217)" in message
    assert "'eur'" in message


@pytest.mark.parametrize("latitude", [90.0001, -91, 180])
def test_out_of_range_latitude_is_rejected(tmp_path: Path, latitude: float) -> None:
    path = write_cities(tmp_path, city(latitude=latitude))

    with pytest.raises(CityConfigError, match=r"cities\[0\] \(rome\)\.latitude"):
        load_cities(path)


def test_out_of_range_longitude_is_rejected(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(longitude=-180.5))

    with pytest.raises(CityConfigError, match=r"\(rome\)\.longitude"):
        load_cities(path)


def test_unknown_timezone_is_rejected(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(timezone="Europe/Atlantis"))

    with pytest.raises(CityConfigError, match="must be a valid IANA time zone.*Europe/Atlantis"):
        load_cities(path)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("id", "New York", "lowercase kebab-case slug"),
        ("id", "new--york", "lowercase kebab-case slug"),
        ("country_code", "ita", "2 uppercase letters"),
        ("rate_source", "fed", "'ecb' or 'cbar'"),
        ("inside_airbnb", "yes", "valid boolean"),
    ],
)
def test_other_invalid_fields_are_rejected(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    path = write_cities(tmp_path, city(**{field: value}))

    with pytest.raises(CityConfigError, match=expected):
        load_cities(path)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(population=2_800_000))

    with pytest.raises(CityConfigError, match="population"):
        load_cities(path)


def test_inside_airbnb_may_be_null(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(inside_airbnb=None))

    assert load_cities(path)[0].inside_airbnb is None


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CityConfigError, match="cities file not found"):
        load_cities(tmp_path / "nope.yaml")


def test_empty_city_list_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cities.yaml"
    path.write_text("cities: []\n")

    with pytest.raises(CityConfigError):
        load_cities(path)


def test_real_file_geonames_ids() -> None:
    by_id = {c.id: c.geonames_id for c in load_cities()}

    assert by_id["baku"] == 587084
    assert by_id["new-york-city"] == 5128581
    assert len(set(by_id.values())) == 10


def test_duplicate_geonames_id_is_rejected(tmp_path: Path) -> None:
    path = write_cities(tmp_path, city(), city(id="roma"))

    with pytest.raises(CityConfigError, match="duplicate geonames_id\\(s\\): 3169070"):
        load_cities(path)


@pytest.mark.parametrize("value", [0, -5, "abc"])
def test_invalid_geonames_id_is_rejected(tmp_path: Path, value: object) -> None:
    path = write_cities(tmp_path, city(geonames_id=value))

    with pytest.raises(CityConfigError, match=r"\(rome\)\.geonames_id"):
        load_cities(path)
