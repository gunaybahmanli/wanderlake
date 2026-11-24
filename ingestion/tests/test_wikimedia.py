"""Wikimedia city images: licence rules, selection, raw -> bronze (requests-mock, moto, SQLite)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
import requests_mock as rm
from conftest import BUCKET
from pyiceberg.catalog.sql import SqlCatalog

from ingestion.sources.wikimedia import (
    COMMONS_API,
    USER_AGENT,
    WIKIDATA_API,
    WikimediaError,
    build_wikimedia_session,
    canonical_media_url,
    image_metadata,
    ingest_snapshot,
    license_family,
    load_bronze,
    p18_candidates,
    select_image,
)

SNAPSHOT = date(2026, 11, 23)


# --- licences -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("short", "code", "family"),
    [
        ("CC BY-SA 4.0", "cc-by-sa-4.0", "cc-by-sa"),
        ("CC BY-SA 3.0", None, "cc-by-sa"),
        ("CC BY-SA 2.0 de", "cc-by-sa-2.0-de", "cc-by-sa"),
        ("CC BY-SA 3.0 IGO", "cc-by-sa-3.0-igo", "cc-by-sa"),
        ("CC BY 2.0", "cc-by-2.0", "cc-by"),
        ("CC0", "cc0", "cc0"),
        ("Public domain", "pd", "public-domain"),
        ("PD-old", None, "public-domain"),
        ("Attribution", "attribution", "other"),  # Commons attribution-only template
        ("CC BY-NC-SA 4.0", "cc-by-nc-sa-4.0", "other"),
        ("CC BY-ND 2.0", None, "other"),
        ("GFDL", "gfdl", "other"),
        (None, None, "other"),
    ],
)
def test_license_family(short: str | None, code: str | None, family: str) -> None:
    assert license_family(short, code) == family


def test_media_urls_use_one_host_without_tracking_parameters() -> None:
    path = "/wikipedia/commons/thumb/d/dc/A_%28b%29.jpg/1280px-A_%28b%29.jpg"
    returned = (
        f"https://thumb.wikimedia.org{path}?utm_source=commons.wikimedia.org&utm_content=thumbnail"
    )
    assert canonical_media_url(returned) == f"https://upload.wikimedia.org{path}"
    assert (
        canonical_media_url(f"https://upload.wikimedia.org{path}")
        == f"https://upload.wikimedia.org{path}"
    )
    assert canonical_media_url(None) is None


# --- selection ----------------------------------------------------------------------------------


def claims(*files: tuple[str, str]) -> dict[str, Any]:
    return {
        "claims": {
            "P18": [
                {"rank": rank, "mainsnak": {"datavalue": {"value": f, "type": "string"}}}
                for f, rank in files
            ]
        }
    }


def test_candidates_put_preferred_first_and_drop_deprecated() -> None:
    doc = claims(
        ("A.jpg", "normal"), ("B.jpg", "preferred"), ("C.jpg", "deprecated"), ("D.jpg", "normal")
    )
    assert [c["file"] for c in p18_candidates(doc)] == ["B.jpg", "A.jpg", "D.jpg"]
    assert p18_candidates({"claims": {}}) == []
    with pytest.raises(WikimediaError):
        p18_candidates({"error": {"code": "no-such-entity"}})


def imageinfo(
    title: str, short: str, code: str | None, artist: str = "<a href='x'>Ann</a>"
) -> dict:
    return {
        "title": f"File:{title}",
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{title}",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}",
                "thumburl": f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{title}/1280px-{title}",
                "width": 4000,
                "height": 3000,
                "thumbwidth": 1280,
                "thumbheight": 960,
                "mime": "image/jpeg",
                "extmetadata": {
                    "LicenseShortName": {"value": short},
                    **({"License": {"value": code}} if code else {}),
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                    "Artist": {"value": artist},
                    "Credit": {"value": "Own work"},
                    "AttributionRequired": {"value": "true"},
                },
            }
        ],
    }


def commons(*pages: dict) -> dict[str, Any]:
    return {"batchcomplete": True, "query": {"pages": list(pages)}}


def test_first_allowed_image_wins_preferred_first() -> None:
    # Amsterdam: the preferred file has the Commons "Attribution" licence (not allowed).
    candidates = p18_candidates(
        claims(("Sat.jpg", "normal"), ("Air.jpg", "preferred"), ("Canals.png", "normal"))
    )
    metadata = image_metadata(
        commons(
            imageinfo("Air.jpg", "Attribution", "attribution"),
            imageinfo("Sat.jpg", "Attribution", "attribution"),
            imageinfo("Canals.png", "CC BY-SA 4.0", "cc-by-sa-4.0"),
        )
    )
    image = select_image(candidates, metadata, None)
    assert (image["file_name"], image["selection"], image["license_family"]) == (
        "Canals.png",
        "normal",
        "cc-by-sa",
    )
    assert image["thumb_width"] == 1280 and image["attribution_required"] is True


def test_without_an_allowed_image_the_first_is_kept_to_fail_the_check() -> None:
    candidates = p18_candidates(claims(("Air.jpg", "preferred")))
    metadata = image_metadata(commons(imageinfo("Air.jpg", "Attribution", "attribution")))
    image = select_image(candidates, metadata, None)
    assert (image["file_name"], image["license_family"]) == ("Air.jpg", "other")


def test_override_is_used_even_when_wikidata_has_other_files() -> None:
    metadata = image_metadata(commons(imageinfo("My choice.jpg", "CC BY 2.0", "cc-by-2.0")))
    image = select_image([{"file": "Other.jpg", "rank": "preferred"}], metadata, "My_choice.jpg")
    assert (image["file_name"], image["selection"], image["license_family"]) == (
        "My choice.jpg",
        "override",
        "cc-by",
    )
    with pytest.raises(WikimediaError, match="not found on Commons"):
        select_image([], {}, "Missing.jpg")


def test_no_candidates_gives_an_empty_row() -> None:
    image = select_image([], {}, None)
    assert image["file_name"] is None and image["selection"] == "none"
    assert image["license_family"] == "other"


def test_session_identifies_the_project() -> None:
    ua = build_wikimedia_session().headers["User-Agent"]
    assert ua == USER_AGENT and "github.com/gunaybahmanli/wanderlake" in ua


# --- raw -> bronze ------------------------------------------------------------------------------

CITIES = [
    SimpleNamespace(id="rome", wikidata_id="Q220", cover_image_file=None),
    SimpleNamespace(id="amsterdam", wikidata_id="Q727", cover_image_file=None),
    SimpleNamespace(id="paris", wikidata_id="Q90", cover_image_file="Paris.jpg"),
]


@pytest.fixture
def wikimedia(requests_mock: rm.Mocker) -> rm.Mocker:
    by_entity = {
        "Q220": claims(("Rome Skyline.jpg", "normal")),
        "Q727": claims(("Air.jpg", "preferred"), ("Canals.png", "normal")),
        "Q90": claims(("Eiffel.jpg", "preferred")),
    }
    requests_mock.get(
        WIKIDATA_API,
        json=lambda req, ctx: by_entity[req.qs["entity"][0].upper()],
    )
    pages = {
        "file:rome skyline.jpg": imageinfo("Rome Skyline.jpg", "CC BY-SA 2.0", "cc-by-sa-2.0"),
        "file:air.jpg": imageinfo("Air.jpg", "Attribution", "attribution"),
        "file:canals.png": imageinfo("Canals.png", "CC BY-SA 4.0", "cc-by-sa-4.0"),
        "file:paris.jpg": imageinfo("Paris.jpg", "CC0", "cc0"),
    }
    requests_mock.get(
        COMMONS_API,
        json=lambda req, ctx: commons(*(pages[t] for t in req.qs["titles"][0].split("|"))),
    )
    return requests_mock


def test_ingest_and_load_bronze(wikimedia: rm.Mocker, s3: Any, catalog: SqlCatalog) -> None:
    result = ingest_snapshot(SNAPSHOT, bucket=BUCKET, cities=CITIES, s3=s3, pause_seconds=0)
    assert result["with_candidates"] == 3
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"])
    assert "wikimedia/wikidata/snapshot_date=2026-11-23/rome.json" in keys
    assert "wikimedia/commons/snapshot_date=2026-11-23/amsterdam.json" in keys
    # Etiquette: every request identifies the project and Wikidata calls set maxlag.
    assert all(r.headers["User-Agent"] == USER_AGENT for r in wikimedia.request_history)
    assert all(
        r.qs.get("maxlag") == ["5"] for r in wikimedia.request_history if "wikidata" in r.url
    )

    loaded = load_bronze(
        SNAPSHOT, dag_run_id="test", bucket=BUCKET, cities=CITIES, catalog=catalog, s3=s3
    )
    assert loaded["rows"] == 3 and loaded["without_open_licence"] == "none"
    rows = {
        r["city_id"]: r
        for r in catalog.load_table("bronze.wikimedia_city_images").scan().to_arrow().to_pylist()
    }
    assert rows["rome"]["file_name"] == "Rome Skyline.jpg"
    assert rows["amsterdam"]["file_name"] == "Canals.png"  # first allowed, not the preferred
    assert rows["paris"]["selection"] == "override" and rows["paris"]["license_family"] == "cc0"
    assert json.loads(rows["amsterdam"]["candidates_json"])[0] == {
        "file": "Air.jpg",
        "rank": "preferred",
    }

    # Reloading the same snapshot replaces it.
    again = load_bronze(
        SNAPSHOT, dag_run_id="test", bucket=BUCKET, cities=CITIES, catalog=catalog, s3=s3
    )
    assert again["rows"] == 3
    assert catalog.load_table("bronze.wikimedia_city_images").scan().to_arrow().num_rows == 3


def test_load_without_raw_files_fails(s3: Any, catalog: SqlCatalog) -> None:
    with pytest.raises(WikimediaError, match="no raw Wikidata claims for rome"):
        load_bronze(
            SNAPSHOT, dag_run_id=None, bucket=BUCKET, cities=CITIES[:1], catalog=catalog, s3=s3
        )
