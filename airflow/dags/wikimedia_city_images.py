"""City cover images from Wikimedia Commons via Wikidata (weekly)."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from wanderlake_assets import WIKIMEDIA_CITY_IMAGES

RAW_BUCKET = "raw"

DOC_MD = """
### Wikimedia city images to bronze

One cover image per configured city, with its licence and attribution, from
[Wikimedia Commons](https://commons.wikimedia.org) through [Wikidata](https://www.wikidata.org)
([ADR-0011](docs/adr/0011-city-images-from-wikimedia-commons-via-wikidata.md)).

1. **ingest:** for each city in `config/cities.yaml`, the `wikidata_id` item's main images
   (P18, Wikidata `wbgetclaims`), then the Commons metadata of those files (`imageinfo`: file
   page, 1280 px thumbnail, sizes, licence, artist, credit). Both responses are stored unchanged
   under `s3://raw/wikimedia/{wikidata,commons}/snapshot_date=YYYY-MM-DD/<city_id>.json`.
   An optional `cover_image_file` in the config replaces the Wikidata choice.
2. **load_bronze:** picks the image (the override, else the first P18 file with an allowed
   licence, preferred rank first) and replaces that snapshot in
   `iceberg.bronze.wikimedia_city_images`. Updates the asset, which triggers
   `wanderlake_transform` and then `publish_cloud`.

- **Licences:** only CC0, public domain, CC BY and CC BY-SA are allowed. A city without an
  allowed image keeps its first candidate with `license_family = other`, so the dbt tests on
  `gold.city_images` fail and name it.
- **Etiquette:** a descriptive User-Agent with the repository URL, one request at a time with a
  1 s pause, `maxlag=5` on Wikidata, retries with backoff: about 20 requests a week.
- **Schedule:** Mondays 02:00 UTC. Trigger it by hand after changing `wikidata_id` or
  `cover_image_file`.

Code lives in `ingestion.sources.wikimedia`.
"""


@dag(
    dag_id="wikimedia_city_images",
    schedule="0 2 * * 1",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "wanderlake", "retries": 3, "retry_delay": timedelta(minutes=10)},
    tags=["bronze", "images", "wikimedia"],
    doc_md=DOC_MD,
)
def wikimedia_city_images():
    def snapshot_date():
        # Manual runs in Airflow 3 can have no logical date: fall back to the run time.
        context = get_current_context()
        return (context.get("logical_date") or context["dag_run"].run_after).date()

    @task
    def ingest() -> dict:
        from ingestion.sources.wikimedia import ingest_snapshot

        return ingest_snapshot(snapshot_date(), bucket=RAW_BUCKET)

    @task(outlets=[WIKIMEDIA_CITY_IMAGES])
    def load_bronze() -> dict:
        from ingestion.sources.wikimedia import load_bronze as load

        run_id = get_current_context()["dag_run"].run_id
        return load(snapshot_date(), dag_run_id=run_id, bucket=RAW_BUCKET)

    ingest() >> load_bronze()


wikimedia_city_images()
