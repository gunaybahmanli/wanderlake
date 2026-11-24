# Wanderlake

[![CI](https://github.com/gunaybahmanli/wanderlake/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gunaybahmanli/wanderlake/actions/workflows/ci.yml)

**Live site:** https://wanderlake.vercel.app (goes live with the first deploy from `main`)

Wanderlake is a global travel intelligence platform built on a self-made, fully open-source lakehouse. It pulls travel data from many sources (flights, weather, exchange rates, destinations, events and more) through batch and streaming pipelines, lands it in Apache Iceberg tables on object storage, models it into clean bronze, silver and gold layers, and serves it through an AI assistant, a public API, a website and analytics dashboards. The whole stack runs locally in Docker on WSL2, and an always-on version is published on free cloud tiers. It is built like a production system: infrastructure as code, CI/CD, data quality tests, observability, architecture decision records and runbooks.

## What it does

- Ingests travel-related data from public APIs, files and operational databases on a schedule (Airflow) and in real time (Kafka + Debezium CDC).
- Stores everything in an open lakehouse: Apache Iceberg tables on S3-compatible storage (SeaweedFS locally, Cloudflare R2 in the cloud), queried with Trino and Spark.
- Transforms raw data into tested, documented bronze, silver and gold models with dbt.
- Answers natural-language travel questions through an AI layer (Ollama, RAG over pgvector, and an MCP server exposing lakehouse tools).
- Serves insights through a FastAPI backend, a Next.js website, and Evidence.dev / Superset dashboards.

## Architecture

```
  Sources                 Ingestion                  Lakehouse (Apache Iceberg on SeaweedFS / R2)    Consumers
  -------                 ---------                  ----------------------------------------        ---------
  Public APIs   --+                                                                               +-->  AI layer (Ollama, RAG, MCP)
  Files / dumps --+-->  Airflow (batch)   --+                                                     |
                  |                         +-->  bronze  -->  silver  -->  gold  --(Trino)--------+-->  FastAPI backend --> Next.js website
  Postgres (OLTP) +-->  Debezium -> Kafka --+        (Spark jobs + dbt models)                     |
                                                                                                  +-->  Evidence.dev / Superset dashboards
```

- **Sources:** public travel APIs, static datasets, and an operational Postgres database.
- **Ingestion:** Airflow orchestrates batch loads; Debezium captures database changes into Kafka for streaming.
- **Storage and processing:** Spark writes Iceberg tables to object storage; dbt (on Trino) builds silver and gold models.
- **Serving:** Trino for SQL, Postgres + pgvector for embeddings, FastAPI for the public API.
- **Consumers:** an AI assistant, a website, and dashboards.

Design decisions and their trade-offs are recorded as [Architecture Decision Records](docs/adr/README.md).

## Tech stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow |
| Streaming and CDC | Apache Kafka, Debezium |
| Processing | Apache Spark |
| Table format | Apache Iceberg |
| Object storage | SeaweedFS (local, S3-compatible), Cloudflare R2 (cloud) |
| Query engine | Trino |
| Transformation | dbt |
| Operational DB and vectors | Postgres + pgvector (local), Supabase (cloud) |
| AI | Ollama, RAG, MCP server |
| Backend | FastAPI |
| Frontend | Next.js (hosted on Vercel) |
| Dashboards | Evidence.dev, Apache Superset |
| Infrastructure | Docker Compose, OpenTofu |
| CI/CD | GitHub Actions |
| Language tooling | Python (uv), TypeScript (Node.js) |

## Status

Phase 0: foundation in progress.

## Roadmap

- **Phase 0: Foundation.** Repository structure, Docker Compose core profile (Postgres + pgvector, SeaweedFS, Airflow), CI skeleton, ADRs.
- **Phase 1: Batch lakehouse.** Ingestion package, Airflow DAGs, Spark jobs, Iceberg bronze/silver/gold, Trino, dbt models and tests.
- **Phase 2: Streaming and CDC.** Kafka, Debezium, streaming ingestion into Iceberg.
- **Phase 3: AI layer.** pgvector embeddings, Ollama, RAG pipeline, MCP server.
- **Phase 4: Products.** FastAPI backend, Next.js website, Evidence.dev and Superset dashboards.
- **Phase 5: Cloud and production hardening.** Cloudflare R2, Supabase, Vercel, GitHub Actions deployments, OpenTofu, observability.

## Run locally

Prerequisites: Docker with Docker Compose v2, `make`, and about 3 GB of free RAM for the `core` profile.

1. Create your local environment file and replace every `change-me` value:

   ```bash
   cp .env.example .env
   ```

2. Start the services. Profiles group them so the stack fits in about 4 GB of RAM:

   | Profile | Services |
   |---|---|
   | `core` | Postgres + pgvector, SeaweedFS, Airflow |
   | `query` | Trino (Iceberg lakehouse engine) |

   ```bash
   make up                       # same as: make up PROFILE=core
   make up PROFILE="core query"  # core plus Trino
   make ps                       # wait until every service is "healthy" (Airflow takes 1-2 minutes)
   ```

   Airflow runs on a custom image, `wanderlake-airflow:local`, built from [`infra/airflow/`](infra/airflow/): the official Airflow 3.4.0 image plus PyIceberg, Astronomer Cosmos, and dbt-trino in a separate virtualenv ([ADR-0007](docs/adr/0007-custom-airflow-image.md)). `make up` builds it the first time; after changing anything in `infra/airflow/`, run `make build` and then `make up` again.

   **Memory tips (4 GB machines).** Airflow and Trino together use most of a 4 GB WSL machine, so switch between three modes instead of leaving everything running:

   | Mode | Running | Use it for | Memory (measured) |
   |---|---|---|---|
   | `make mode-ingest` | postgres, seaweedfs, airflow (trino stopped) | ingestion DAGs, backfills | about 1.3 GiB idle |
   | `make mode-query` | postgres, seaweedfs, trino (airflow stopped) | Trino queries, `make dbt-*` | about 1.1 GiB idle |
   | `make mode-full` | postgres, seaweedfs, airflow, trino | `wanderlake_transform` (dbt from Airflow needs Trino) | peak 2.4 GiB during a transform run (airflow 1.36 GiB, trino 1.04 GiB); WSL `available` never below 676 MB |

   - Full mode is the tightest: run it for `wanderlake_transform` (about 8-10 minutes) and go back to `make mode-ingest` afterwards. If WSL `available` memory (`free -m`) falls below about 500 MB, stop Trino first.
   - All modes work whether the containers exist or not, and they wait until the services are healthy. On a brand-new install, run `make up` once first: it also runs the one-shot bucket setup.
   - Trino does not restart on its own (`restart: "no"`), so a WSL restart never brings the whole stack back at once.
   - Run backfills one date at a time (`MAX_ACTIVE_RUNS=1`, the default).
   - **One Airflow task at a time** (`AIRFLOW__CORE__PARALLELISM=1`, `mem_limit: 2g`). Airflow idles at about 1.0 GiB, and every running task adds about 0.45 GiB, because Airflow 3 runs each task in its own supervisor and worker processes (the GeoNames load measured 1.48 GiB at its peak). Two tasks at once, such as `frankfurter_rates` and `cbar_rates` at 16:30, could pass 1.9 GiB, so tasks queue instead; that costs about a minute a day. This is sized for an 8 GB laptop with WSL limited to 4 GB: **revisit both settings after the RAM upgrade.**
   - Stop the stack (`make down`) before building images (`make build`).

3. Open the services:

   | Service | URL / port | Login |
   |---|---|---|
   | Airflow UI | http://localhost:8080 | `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` from `.env` |
   | S3 API (SeaweedFS) | http://localhost:8333 (path-style) | `S3_ACCESS_KEY` / `S3_SECRET_KEY` from `.env` |
   | SeaweedFS filer UI | http://localhost:8888/buckets/ | none (read-only file browser, local only) |
   | SeaweedFS master UI | http://localhost:9333 | none (cluster status) |
   | Postgres | `localhost:5432`, database `wanderlake` | `POSTGRES_USER` / `POSTGRES_PASSWORD` from `.env` |
   | Trino (profile `query`) | http://localhost:8081 (web UI), or `make trino` | any user name, no password (local only) |

   On first start, the one-shot `s3-init` container creates the `raw` and `lakehouse` buckets.

4. Useful commands:

   ```bash
   make logs                  # follow all logs
   make logs SERVICE=airflow  # follow one service
   make psql                  # psql shell in the wanderlake database
   ```

5. Stop everything (data is kept in Docker volumes):

   ```bash
   make down
   ```

   `make down` stops the services of every profile. To also delete all data, run `docker compose --env-file .env -f infra/compose/compose.yaml --profile '*' down -v`.

### Browsing object storage from the command line

SeaweedFS has no MinIO-style console. Use any S3 client with path-style addressing, for example the AWS CLI in Docker (no local install needed):

```bash
set -a; . ./.env; set +a
aws_s3() {
  docker run --rm --network host \
    -e AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" -e AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" \
    -e AWS_DEFAULT_REGION="$S3_REGION" -e AWS_ENDPOINT_URL="$S3_ENDPOINT" \
    amazon/aws-cli:2.41.0 s3 "$@"
}
aws_s3 ls                          # list buckets
aws_s3 ls s3://raw --recursive     # list files in a bucket
```

Or use the SeaweedFS shell inside the container:

```bash
docker exec -it wanderlake-seaweedfs-1 weed shell
> s3.bucket.list
> fs.ls /buckets/raw
```

## Lakehouse

Lakehouse tables are [Apache Iceberg](https://iceberg.apache.org/) tables queried with Trino ([ADR-0003](docs/adr/0003-use-iceberg-with-sql-catalog.md)):

- **Catalog:** Iceberg JDBC catalog in Postgres (database `iceberg_catalog`, its own role).
- **Data files:** the SeaweedFS bucket `lakehouse`, under `s3://lakehouse/warehouse/`.
- **Layers** (one Iceberg schema each): `bronze` (raw data as delivered), `silver` (cleaned and conformed), `gold` (modelled for the API, website and dashboards).

```bash
make up PROFILE="core query"   # Trino needs Postgres and SeaweedFS from core
make lakehouse-init            # create the bronze, silver and gold schemas (idempotent)
make lakehouse-smoke-test      # write, read and drop a test table; shows its files
make trino                     # interactive Trino shell on the iceberg catalog
```

Example queries in the Trino shell:

```sql
SHOW SCHEMAS FROM iceberg;
SHOW TABLES FROM iceberg.bronze;
SELECT * FROM iceberg.bronze."<table>$snapshots";   -- Iceberg table history
```


## Transformations (dbt)

dbt (`dbt-core` + `dbt-trino`, from the Airflow image's `/opt/dbt-venv`) builds the silver and gold layers on Trino and Iceberg. The project is in [`dbt/`](dbt/).

| Layer | What | Materialization |
|---|---|---|
| bronze | data as received (loaded by the Airflow DAGs); declared as dbt sources with freshness checks | Iceberg tables |
| silver (`stg_*`) | staging: rename, cast, deduplicate weekend/holiday repeats | views |
| silver | cleaned, conformed tables (below) | tables with enforced contracts |
| gold | star schema and serving tables for the website, API and dashboards (below) | tables with enforced contracts; `fct_weather_daily` incremental |

Silver tables (all with enforced contracts, keys tested for uniqueness, descriptions on every column; browse them with `make dbt-docs`):

| Table | One row per | Built from |
|---|---|---|
| `silver.exchange_rates` | `rate_date`, `currency_code` | ECB rates, CBAR for currencies ECB does not publish; EUR base ([ADR-0008](docs/adr/0008-unified-exchange-rates.md)) |
| `silver.countries` | country (`cca2`) | latest REST Countries snapshot; currencies and languages flattened from JSON (primary currency = first listed) |
| `silver.cities` | configured city | `config/cities.yaml` joined to the latest GeoNames snapshot; `elevation_m` falls back to GeoNames' terrain model (`elevation_source`) |
| `silver.weather_daily` | `city_id`, `weather_date` | Open-Meteo history, with WMO descriptions from the seed `wmo_weather_codes` |
| `silver.weather_forecast` | `run_date`, `city_id`, `forecast_date` | Open-Meteo forecasts; `is_latest_run` marks the newest run |
| `silver.travel_advice` | country and published version | FCDO advice change history (`valid_from`, `valid_to`, `is_current`), alerts, and plain-text sections |

**Travel advice text:** the HTML sections are converted to plain text in SQL (`dbt/macros/html_to_text.sql`): tags removed, common and numeric entities decoded, non-breaking spaces and whitespace normalised. Tables and lists lose their structure and link targets are dropped; a test checks that no tags or entities remain. `summary_text` is the FCDO section `warnings-and-insurance` (the pages have no section called summary); every section is in `full_text`, which phase 2 AI uses.

**Tests** cover unique keys, relationships (cities -> countries, weather -> cities, travel advice -> countries, weather codes -> seed), value ranges (temperatures -60 to 60 C, precipitation >= 0), and completeness (every city has weather history, exactly 7 forecast days in the latest run, one current travel advice row per city country).

### Gold layer (star schema)

Gold is shaped for its consumers: dimensions describe places, facts hold measurements by date, and serving tables give the website one ready row per page. Every gold model has an enforced contract.

```
             dim_country
                  |
              dim_city ------------------+---------------------+
                  |                      |                     |
      fct_weather_daily       agg_city_climate_monthly    city_overview
      (city, day)             (city, month)               (city; serving)

      fct_exchange_rate (date, from_currency, to_currency): currency-level fact
```

| Table | Type | One row per | Notes |
|---|---|---|---|
| `gold.dim_country` | dimension | country (all 250) | `has_configured_city` marks the countries with a configured city |
| `gold.dim_city` | dimension | configured city | `silver.cities` plus country fields (name, region, flag, languages, currency name and symbol) |
| `gold.fct_exchange_rate` | fact | `date`, `from_currency`, `to_currency` | daily conversion matrix (below) |
| `gold.fct_weather_daily` | fact | `city_id`, `weather_date` | daily weather with derived fields: `is_weekend`, `temp_range_c`, `is_rainy_day` (>= 1 mm), `sunshine_hours` |
| `gold.agg_city_climate_monthly` | aggregate | `city_id`, `month` | averages over all loaded years, `pleasant_day_share` (below) |
| `gold.city_overview` | serving | configured city | city fields, rates to EUR, USD and AZN today, today's forecast, current travel advice alert, `best_months` |
| `gold.weather_forecast_latest` | serving | `city_id`, `forecast_date` | 7-day forecast from each city's latest run, with `weather_description` |
| `gold.travel_advice_current` | serving | country with FCDO advice | current version: `has_alert`, `alert_statuses`, `public_updated_at`, `change_description` and the summary, safety, entry requirements and health texts |
| `gold.city_images` | serving | configured city | cover photo from Wikimedia Commons: thumbnail URL and size, author, licence (name, family, link), Commons file page; only open licences ([ADR-0011](docs/adr/0011-city-images-from-wikimedia-commons-via-wikidata.md)) |

- **Conversion matrix and as-of rule:** `fct_exchange_rate` covers the city currencies plus EUR, USD and AZN, every ordered pair of different currencies, for every calendar day from the first rate date to today. Each day uses each currency's latest rate on or before that day (rates are not published on weekends and holidays), so `from_rate_date` and `to_rate_date` can be older than `date`; `source_from` and `source_to` say whether ECB or CBAR supplied each side. `rate` is the number of `to_currency` units per 1 `from_currency`, computed through the EUR base. The day spine is Trino's `sequence()`.
- **Incremental weather:** `fct_weather_daily` merges on (`city_id`, `weather_date`) and reprocesses the last 7 days on each run (Open-Meteo revises recent days); a `--full-refresh` rebuilds the full history.
- **Pleasant day:** maximum temperature between 18 and 28 C (inclusive) and less than 1 mm of precipitation. `pleasant_day_share` is the share of such days in that calendar month over all loaded years; `best_months` in `city_overview` are the 3 months with the highest share (ties go to the earlier month).
- **Today** in `city_overview` is the city's own date (its time zone) for the forecast, from the latest forecast run; rates are today's as-of rates.
- **Exposures** (`dbt/models/exposures.yml`) declare the consumers: the website (https://wanderlake.vercel.app) and the planned dashboard, so `make dbt-docs` shows which models feed them.

**Gold tests** add: composite keys unique, relationships to `dim_city` and `dim_country`, reciprocal rates (A->B times B->A between 0.999 and 1.001), 100 USD in AZN today between 169 and 171, every configured currency present today, 12 months per city in the climate aggregate, and exactly one `city_overview` row per city with today's forecast.

In production the DAG `wanderlake_transform` runs dbt (see [Daily flow](#daily-flow)). By hand, dbt runs in a one-off container of the compose service `dbt` (our Airflow image, project mounted read-write) and needs Trino, not Airflow:

```bash
make mode-query              # postgres + seaweedfs + trino
make config-load             # copy config/cities.yaml to bronze.config_cities (also a daily DAG)
make dbt-deps                # install dbt packages (dbt_utils)
make dbt-manifest            # dbt parse -> dbt/target/manifest.json, read by wanderlake_transform
make dbt-build               # build all models and run all tests
make dbt-test                # tests only
make dbt-source-freshness    # are the bronze sources up to date?
make dbt-docs                # generate the docs and serve them on http://localhost:8082
make mode-ingest             # back to Airflow
```

- Models land in exactly the schema they declare (`silver`, `gold`); `profiles.yml` takes every connection value from environment variables.
- `bronze.config_cities` is a full copy of `config/cities.yaml` for SQL (the `config_cities` DAG and `make config-load` refresh it); the YAML stays the single source of truth.
- SQL is linted by sqlfluff with the jinja templater and dbt builtins; the "dbt parse" CI job checks the project without a database.
- **Run `make dbt-manifest` after changing models, tests or seeds** (no database needed): `wanderlake_transform` renders its tasks from `dbt/target/manifest.json` and never runs dbt while parsing, so until then it keeps the old graph. Any `make dbt-*` command also rewrites the manifest.
- On Linux/WSL, `AIRFLOW_UID` in `.env` must be your user id (`id -u`), so the `dbt` container can write `dbt/target`, `dbt/logs` and `dbt/dbt_packages`. The Airflow container mounts `dbt/` read-only: Cosmos runs every dbt command in a temporary copy of the project.

## Cities

[`config/cities.yaml`](config/cities.yaml) is the single source of truth for the cities Wanderlake covers. Every city-aware connector reads it through `ingestion.config.load_cities()`, which validates it (unique slug ids, ISO country and currency codes, coordinate ranges, IANA time zone, `rate_source`).

To add a city:

1. Add an entry to `config/cities.yaml` with all fields: `id` (lowercase kebab-case, e.g. `new-york-city`), `name`, `country_code` (ISO 3166-1 alpha-2), `timezone` (IANA, e.g. `Asia/Baku`), `latitude` and `longitude` (city centre, 4 decimals), `currency` (ISO 4217).
2. Set `rate_source`: `ecb` if the currency is EUR or appears in the [ECB euro reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), otherwise `cbar`.
3. Set `inside_airbnb`: `true` if the city is on the Inside Airbnb [Get the Data](https://insideairbnb.com/get-the-data/) page, `false` if not, `null` if not checked yet.
4. Run `make test`: the config tests fail with a message naming the city and field if anything is wrong. Update the expected id list in `ingestion/tests/test_config.py`.

The Airflow container mounts `config/` read-only at `/opt/wanderlake/config`, so DAGs read the same file.

## Pipelines

| DAG | Schedule (UTC) | Source | Output (bronze) |
|---|---|---|---|
| `frankfurter_rates` | daily 16:30 | [Frankfurter API](https://frankfurter.dev), ECB reference rates, base EUR | raw `s3://raw/frankfurter/rates/date=YYYY-MM-DD/rates.json`, then bronze `iceberg.bronze.frankfurter_rates` |
| `cbar_rates` | daily 16:30 | [CBAR](https://www.cbar.az) official rates, AZN per foreign unit | raw `s3://raw/cbar/rates/date=YYYY-MM-DD/rates.xml`, then bronze `iceberg.bronze.cbar_rates` |
| `rest_countries` | weekly, Mon 03:00 | [REST Countries](https://restcountries.com) v5 reference data (API key) | raw `s3://raw/rest_countries/all/snapshot_date=YYYY-MM-DD/page=N.json`, then bronze `iceberg.bronze.rest_countries` |
| `geonames_cities` | monthly, 1st 04:00 | [GeoNames](https://www.geonames.org) dump `cities15000.zip` (CC BY 4.0) | raw `s3://raw/geonames/cities15000/snapshot_date=YYYY-MM-DD/cities15000.zip`, then bronze `iceberg.bronze.geonames_cities` |
| `open_meteo` | daily 05:00 | [Open-Meteo](https://open-meteo.com) forecast (7 days) and archive (last 7 days); CC BY 4.0 | raw `s3://raw/open_meteo/{forecast,history}/...`, then bronze `iceberg.bronze.open_meteo_forecast` and `iceberg.bronze.open_meteo_history` |
| `fcdo_advice` | daily 06:00 | [GOV.UK](https://www.gov.uk/foreign-travel-advice) FCDO foreign travel advice (OGL v3.0) | raw `s3://raw/fcdo/advice/snapshot_date=YYYY-MM-DD/<country_code>.json`, then bronze `iceberg.bronze.fcdo_advice` |
| `config_cities` | daily 00:30, and on demand | `config/cities.yaml` (this repo) | `iceberg.bronze.config_cities` (full replace; no raw copy, the file is in git) |
| `wikimedia_city_images` | weekly, Mon 02:00 | [Wikidata](https://www.wikidata.org) (CC0) and [Wikimedia Commons](https://commons.wikimedia.org) (each image under its own open licence) | raw `s3://raw/wikimedia/{wikidata,commons}/snapshot_date=YYYY-MM-DD/<city_id>.json`, then bronze `iceberg.bronze.wikimedia_city_images` |
| `wanderlake_transform` | when the daily bronze assets are updated, or the weekly city images, or 18:00 fallback | the bronze tables above | dbt silver and gold (Cosmos), then the `gold` asset |
| `publish_cloud` | when the `gold` asset is updated (no clock time) | the gold tables | Supabase schema `api` (read-only REST API) and R2 Parquet; see [Cloud publish](#cloud-publish) |

### Daily flow

Every load task above declares its bronze table as an Airflow **Asset** (`iceberg://wanderlake/bronze/<table>`, defined in [`airflow/dags/wanderlake_assets.py`](airflow/dags/wanderlake_assets.py)); a successful load records an asset event. `wanderlake_transform` is scheduled on those events ([ADR-0009](docs/adr/0009-orchestrate-dbt-with-cosmos-and-assets.md)):

```
05:00  open_meteo ---- load_forecast -> bronze.open_meteo_forecast --+
                    \- load_history  -> bronze.open_meteo_history ---+
16:30  frankfurter_rates - load_bronze -> bronze.frankfurter_rates --+-- all four updated --> wanderlake_transform
16:30  cbar_rates ------- load_bronze -> bronze.cbar_rates ---------+   (or 18:00 UTC fallback)       |
                                                                                                     v
Mon 02:00 wikimedia_city_images - load_bronze -> bronze.wikimedia_city_images --- or on its own -->|
       rest_countries, geonames_cities, fcdo_advice, config_cities: assets too, never block      gold asset
                                                                                                     |
                                                     Supabase api (REST) + R2 Parquet <-- publish_cloud
```

- **Schedule:** `AssetOrTimeSchedule`: a run when all four daily inputs have new events since the last asset-triggered run, or every day at 18:00 UTC. On a normal day the asset run starts at about 16:35 UTC. A fallback run first checks the `gold` asset and skips when gold was built in the last 20 hours, so the fallback only builds when ingestion failed or was late. Weekly and monthly sources (and FCDO) are read at their latest state.
- **Tasks** (Astronomer Cosmos, one per dbt node): `gate` >> `source_freshness` (warnings logged, errors fail the run) >> `seeds` >> `models` (each model's `run` task followed by its `test` task; tests on several models run after all of them) >> `publish_gold`. A failing model or test stops only what depends on it, and a retry reruns only that node.
- **Needs** `make mode-full` (Airflow and Trino together). Unpausing the DAG runs the latest missed 18:00 slot once (`catchup=False`).
- **Then `publish_cloud`** runs on the `gold` asset event, so every successful transform is published once (below).

### Cloud publish

`publish_cloud` copies the gold tables the website needs from the laptop's lakehouse to free cloud services ([ADR-0010](docs/adr/0010-serve-site-from-supabase-rest-with-read-only-rls.md)). Code: [`ingestion/src/ingestion/publish/`](ingestion/src/ingestion/publish/).

| Table | Supabase `api.<table>` | R2 (`wanderlake-lake`) |
|---|---|---|
| `dim_city`, `dim_country`, `city_overview`, `fct_weather_daily`, `agg_city_climate_monthly`, `weather_forecast_latest`, `travel_advice_current`, `city_images` | full table | `gold/<table>/snapshot_date=YYYY-MM-DD/data.parquet` and `gold/<table>/latest/data.parquet` |
| `fct_exchange_rate` | last 400 days | same layout, last 400 days |

- **Tasks:** `apply_migrations` (new SQL files in [`supabase/migrations/`](supabase/migrations/): schema `api`, tables, RLS, grants) >> `publish_table` (one mapped task per table) >> `verify_counts`.
- **Per table:** read `gold.<table>` with PyIceberg (no Trino needed), write both Parquet files to R2, then replace `api.<table>` in **one transaction** (COPY into a temporary staging table, delete, insert): API readers see the old rows or the new rows, never a partial table.
- **Verification:** source rows, Supabase `count(*)` and the row counts of both Parquet files read back from R2 must be equal for every table, or the run fails with the full table of counts.
- **Read-only public API:** the website reads `https://<ref>.supabase.co/rest/v1/<table>` with the anon key and the header `Accept-Profile: api`. Row-level security with a SELECT-only policy plus SELECT-only grants mean the anon key can read and nothing else; OpenTofu (`supabase_settings`) exposes the schema.

```bash
make mode-full     # or mode-ingest: the job itself needs only Airflow
make publish       # same code as the DAG; prints source vs Supabase vs R2 counts
make api-check     # anon SELECT works; INSERT, UPDATE, DELETE are rejected
```

Credentials (`CLOUD_S3_*`, `SUPABASE_DB_*`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`) come from `.env`; the [cloud runbook](docs/runbooks/cloud-setup.md) covers creating and rotating them. A published gold column is part of the API: change it with a new Supabase migration in the same change (a test compares the migrations with the dbt contracts).

### Production ingestion (GitHub Actions)

Dev is the laptop (Airflow and Cosmos on SeaweedFS). Prod is the workflow [`prod-pipeline.yml`](.github/workflows/prod-pipeline.yml), which runs the **same code** nightly, configured only by environment variables (`WANDERLAKE_ENV=prod`). The architecture decision (ADR-0012) and a runbook follow with the transform and publish step.

| | Dev (laptop) | Prod (GitHub Actions) |
|---|---|---|
| Runs | one Airflow DAG per source | `python -m ingestion.run daily` at 17:15 UTC, or manually |
| Raw responses | SeaweedFS bucket `raw` | R2 bucket `wanderlake-raw` |
| Iceberg data | `s3://lakehouse/warehouse/` | `s3://wanderlake-lake/warehouse/` (R2) |
| Iceberg catalog | Postgres database `iceberg_catalog` | Supabase Postgres, schema `iceberg_catalog` (role `wanderlake_catalog`; not exposed through the REST API), same catalog name `wanderlake` |
| Trino | Compose service, 768 MB heap | throwaway container in the job: 10 GB limit, 8 GB heap, 4 GB per query ([config](infra/prod/trino/etc/config.properties)) |

- **Runner** ([`ingestion/src/ingestion/pipeline.py`](ingestion/src/ingestion/pipeline.py)): config, GeoNames (monthly), REST Countries (weekly), FCDO, Wikimedia (weekly), Frankfurter, CBAR, Open-Meteo forecast and recent history, calling the same `ingest_*`/`load_*` functions as the DAGs. Weekly and monthly sources run only when their latest bronze snapshot is older than their period, so reruns are safe. A failing source is retried twice (60 s apart) and does not stop the others; a source that asks to come back later (Wikidata `maxlag`) is reported as deferred and stays due.
- **Workflow:** environment `production` (secrets), never two runs at once (concurrency group, no cancelling), actions pinned to commits, read-only token. It starts Trino with the prod catalog, creates the schemas if needed, runs the runner, and writes a job summary: rows loaded per source and row counts of every bronze table.
- **Manual runs:** *Actions → Prod pipeline → Run workflow*, mode `daily` (optional date) or `backfill` (Frankfurter and CBAR day by day for 400 days, Open-Meteo history month by month from 2023-01-01, then current snapshots of every source). Same with the CLI: `gh workflow run prod-pipeline.yml -f mode=backfill`. To refill a gap in one source, pick it in `only` with `start`/`end` (for example `-f mode=backfill -f only=cbar_rates -f start=2026-07-07 -f end=2026-09-12`); only that part runs. Failures that look temporary (network errors, HTTP 429/5xx, an HTML error page instead of data) are retried twice, 60 s apart; data errors fail at once.
- **One-time setup** (done; idempotent): `make prod-catalog-init` (catalog role, schema and tables in Supabase) and `make prod-github-env` (environment `production`, its allowed branches and secrets from `.env`; values are never printed).

In this phase prod only ingests: nothing is written to the Supabase `api` schema, which the laptop still publishes.

### `frankfurter_rates`

Fetches the European Central Bank reference rates from `https://api.frankfurter.dev/v2/providers/ecb/rates?date=<date>&base=EUR` and stores the response body unchanged. The date is the run's logical date, and re-running a date overwrites the same object.

- On weekends, holidays, or before the ECB publishes, the API returns the last business day. The file is still stored, and the object metadata `rate_date` shows which day the rates are for.
- Each object carries metadata: `ingested_at`, `source_url`, `http_status`, `requested_date`, `rate_date`.
- The code lives in the `ingestion` package (`ingestion/src/ingestion/sources/frankfurter.py`); the DAG only calls it.

**Raw to bronze.** The DAG has two tasks, `ingest >> load_bronze`:

1. `ingest` stores the API response unchanged in the `raw` bucket (one JSON file per requested date).
2. `load_bronze` reads that file and writes one row per quote currency to the Iceberg table `iceberg.bronze.frankfurter_rates`, replacing that date's earlier rows in one atomic commit. Loading the same date again never duplicates rows.

| Column | Meaning |
|---|---|
| `requested_date` | the run's date (what the API was asked for); the table is partitioned by its month |
| `rate_date` | the business day the rates are for |
| `base_currency`, `quote_currency`, `rate` | `rate` units of `quote_currency` per 1 `base_currency` (always EUR); `decimal(18, 8)`, parsed exactly |
| `_ingested_at` | when the raw file was written (from its S3 metadata) |
| `_loaded_at` | when the bronze row was written |
| `_source_file` | the raw file, e.g. `s3://raw/frankfurter/rates/date=2026-11-17/rates.json` |
| `_dag_run_id` | the Airflow run that loaded it |

Bronze keeps the data as received: weekend and holiday runs repeat the last business day's rates under their own `requested_date` (with that day as `rate_date`). Deduplicating by `rate_date` is the silver layer's job.

PyIceberg (in Airflow) and Trino share one Iceberg catalog. Its name, `ICEBERG_CATALOG_NAME` in `.env`, is read by both; if they differed, each would see different tables.

### `cbar_rates`

Fetches the Central Bank of Azerbaijan's official rates from `https://www.cbar.az/currencies/DD.MM.YYYY.xml` (one XML file per date) and follows the same pattern as Frankfurter: `ingest` stores the file unchanged in `raw` with the same S3 metadata, then `load_bronze` replaces that date's rows in `iceberg.bronze.cbar_rates`. Both rate sources run at 16:30 UTC.

- **Rates are AZN for `nominal` units:** most currencies are quoted per 1 unit, some per 100 (e.g. JPY, KRW, KZT), so 1 unit costs `rate_azn / nominal`.
- **Only foreign currencies:** the bank metals (XAU, XAG, XPT, XPD, quoted per troy ounce) and SDR (the IMF's unit of account, not a currency) are skipped and logged.
- **Weekends and holidays** return the last published file; bronze keeps those rows under their own `requested_date`, like Frankfurter.
- **Future dates are refused:** CBAR answers them with today's file instead of an error.
- `currency_name` is kept as published (Azerbaijani, e.g. `100 Yapon yeni`). The XML is parsed with `defusedxml` (no entity expansion, no external entities).

Columns: `requested_date`, `rate_date`, `currency_code`, `currency_name`, `nominal` (int), `rate_azn` (`decimal(18, 6)`), plus the same four ingestion columns as Frankfurter.

### `rest_countries` (reference data)

A weekly full snapshot of country reference data from REST Countries **v5**, which needs a free API key (`REST_COUNTRIES_API_KEY` in `.env`; see the cloud runbook). v1-v4 (`/v3.1/all`) are deprecated and answer HTTP 200 with an error object, so the pipeline accepts only a real v5 country list.

- **Raw:** the list comes in pages of at most 100 records (3 pages, about 254 entities); each page is stored unchanged. A rerun of the same date replaces its pages.
- **Validation (fails loudly):** all pages present, at least 200 countries with an ISO 3166 code, no malformed or duplicate codes. Entities without an ISO code (e.g. Abkhazia) stay in raw but are left out of bronze.
- **Cities check:** every `country_code` in `config/cities.yaml` must be in the snapshot, otherwise `load_bronze` fails before writing.
- **Bronze** (`iceberg.bronze.rest_countries`, partitioned by year): one row per country and `snapshot_date` with names, codes, region, `capital` (the primary capital, else the first), population, area, coordinates, flag URL, `independent` (v5 `sovereign`) and `un_member`. **Nested values stay JSON strings** (`capitals_json`, `languages_json`, `currencies_json`, `timezones_json`); flattening them is the silver layer's job.

### `geonames_cities` (reference data)

A monthly snapshot of the free GeoNames dump of cities with a population above 15,000 or capitals (`https://download.geonames.org/export/dump/cities15000.zip`, about 34,000 places, no account needed).

- **Raw:** the zip is stored unchanged, with `file_size` and `sha256` in its metadata; `load_bronze` refuses a raw zip whose sha256 no longer matches.
- **Parsing:** the tab-separated file is read with PyArrow using the 19 column names and types from the dump's `readme.txt`, no quoting, and empty values as null. Code columns (`admin1_code` etc.) stay strings so leading zeros survive.
- **Validation (fails loudly):** more than 20,000 rows, unique `geonameid`, latitude and longitude in range.
- **Cities link:** every city in `config/cities.yaml` has a `geonames_id`, matched by country, name and the closest distance, preferring the feature codes PPLC/PPLA/PPL (`match_city`). `load_bronze` fails before writing if a configured id is missing from the snapshot.
- **Bronze** (`iceberg.bronze.geonames_cities`, partitioned by year): all 19 GeoNames columns plus `snapshot_date` and the four ingestion columns; `alternatenames` is kept as received.

### `open_meteo` (weather)

Daily weather for every city in `config/cities.yaml`, in one request for all cities (each in its own timezone, metric units). Free API, used under its non-commercial terms (fewer than 10,000 calls a day; this pipeline uses about 22).

| | Forecast | History |
|---|---|---|
| Source | `https://api.open-meteo.com/v1/forecast`, 7 days ahead | `https://archive-api.open-meteo.com/v1/archive` (ERA5/ERA5-Land reanalysis; newest days from ECMWF IFS) |
| Raw | `s3://raw/open_meteo/forecast/run_date=YYYY-MM-DD/forecast.json` | daily `.../history/date=YYYY-MM-DD/history.json`; backfill `.../history/backfill/month=YYYY-MM/history.json` |
| Bronze | `iceberg.bronze.open_meteo_forecast`: `run_date`, `city_id`, `forecast_date`, variables; partitioned by month, replaced per `run_date` | `iceberg.bronze.open_meteo_history`: `city_id`, `weather_date`, variables; partitioned by year, replaced per date range |

- **Variables:** `weather_code`, `temperature_2m_max/min/mean`, `precipitation_sum`, `rain_sum`, `snowfall_sum`, `precipitation_hours`, `wind_speed_10m_max`, `sunshine_duration` (seconds); the forecast adds `uv_index_max` and `precipitation_probability_max`.
- **Recent history is provisional:** the archive's newest days come from a forecast model and are revised when the ERA5 reanalysis arrives (about 5 days later). The daily run therefore re-fetches the last 7 days; reloads replace that date range, so they never duplicate.
- **Validation:** the response must have one location per configured city, in order, with the city's timezone and a model grid point within 30 km. The raw metadata stores the requested `city_ids`, so a raw file can always be mapped back to cities.
- **One-time history backfill** (2023-01-01 until 8 days ago, month by month, pausing 6 s between requests, about 1,000 API calls in total): `make weather-backfill` (`WEATHER_START=YYYY-MM-DD` to change the start). It is idempotent.

### `fcdo_advice` (travel advisories)

A daily snapshot of the UK Foreign, Commonwealth & Development Office travel advice for the countries of our cities, from the GOV.UK Content API (no key; at most 10 requests per second, this DAG makes 11 a day, 1 second apart).

- **Country mapping without a hand-typed list:** GOV.UK has no ISO codes, so each `country_code` is resolved through official names: its `name_common`/`name_official` in the latest `bronze.rest_countries` snapshot, matched to an FCDO country name or one of its synonyms in the index (`https://www.gov.uk/api/content/foreign-travel-advice`). For example, `US` resolves to `usa` through the FCDO synonym "United States". The run fails if a country has no match or more than one (FCDO synonyms include city names). This DAG therefore needs `rest_countries` to have run at least once.
- **Raw:** each country response unchanged, plus the index used for the mapping (`_index.json`).
- **Bronze** (`iceberg.bronze.fcdo_advice`, partitioned by year): one row per country and `snapshot_date` with `slug`, `title`, `public_updated_at`, `change_description`, `alert_status_json` (e.g. `["avoid_all_but_essential_travel_to_parts"]`) and `parts_json` (the sections with their **HTML body unchanged**). Cleaning the HTML to text happens in silver; summaries come in phase 2.

### `wikimedia_city_images` (city cover photos)

A weekly cover photo per configured city from Wikimedia Commons, chosen through Wikidata ([ADR-0011](docs/adr/0011-city-images-from-wikimedia-commons-via-wikidata.md)).

- **Which image:** each city's reviewed `wikidata_id` in `config/cities.yaml` (found through its GeoNames ID, Wikidata property P1566; Baku and Madrid use the main items, see the comments there). The item's main images (P18) are tried preferred rank first, then normal, and the **first one with an allowed licence** is used. An optional `cover_image_file` in the config replaces the choice for a city.
- **Licences:** only CC0, public domain, CC BY and CC BY-SA. A city without an allowed image keeps its first candidate with `license_family = other`, so the dbt tests on `gold.city_images` fail and name it.
- **Raw:** the Wikidata P18 claims (`wbgetclaims`) and the Commons `imageinfo` response (file page, original and 1280 px thumbnail URLs and sizes, licence, artist, credit), unchanged.
- **Bronze** (`iceberg.bronze.wikimedia_city_images`): one row per city and `snapshot_date`: the selected file, how it was selected, the candidates considered, and the metadata (HTML kept; cleaned in silver).
- **Etiquette:** a descriptive User-Agent with the repository URL, one request at a time with a 1 s pause, `maxlag=5` on Wikidata, retries with backoff: about 20 requests a week.
- **Downstream:** its asset triggers `wanderlake_transform` on its own (OR with the daily inputs), then `publish_cloud` publishes `api.city_images`.

Run and inspect it:

```bash
make backfill DAG=frankfurter_rates START=2026-11-11 END=2026-11-17   # one run per day, inclusive
make backfill DAG=frankfurter_rates START=2026-10-20 END=2026-11-19 REPROCESS=completed   # also re-run dates that already ran
make s3-ls                                                            # list objects in the raw bucket
make s3-ls BUCKET=lakehouse                                           # or another bucket
make trino   # then: SELECT * FROM iceberg.bronze.frankfurter_rates ORDER BY requested_date DESC LIMIT 10;
```

Backfill dates are run timestamps, not calendar days: the DAG runs at 16:30 UTC, and `END` means midnight at the start of that day. To include a day's run, set `END` to the **next** day (the example above loads 2026-10-20 up to and including 2026-11-18). `REPROCESS=completed` re-runs dates that already succeeded (in place, under their original run id); `MAX_ACTIVE_RUNS` (default 1) keeps memory use low.

New DAGs start paused: unpause `frankfurter_rates` in the Airflow UI (or `docker exec wanderlake-airflow-1 airflow dags unpause frankfurter_rates`) for the daily schedule to run. You can trigger a single run from the UI with the play button.

## Website

The website (https://wanderlake.vercel.app) is a Next.js app (App Router, TypeScript, plain CSS) in [`web/`](web/), deployed on Vercel. The Vercel project and its environment variables are managed by OpenTofu in `infra/tofu`.

| Page | What it shows |
|---|---|
| `/` | Short intro and "Good to visit this month": cities ranked by their share of pleasant days in the current month |
| `/explore` | A card per city (country and flag, today's weather, best months, currency) with filters for region, travel month and currency; filters live in the URL query (`/explore?region=Asia&month=5`), so links can be shared, and the form works without JavaScript |
| `/cities/<id>` | Facts (country, region, population, time zone, languages, currency, elevation and its source), today's weather, the 7-day forecast, the best months, a climate chart, a currency converter, and UK travel advice (alert badge, summary, last update, link to GOV.UK) |
| `/about` | How the pipeline works, data sources and licences, and when the data was last updated |

Plus `sitemap.xml`, `robots.txt` and per-page titles and descriptions.

- **Currency converter** (client component): from AZN to the city's currency by default (AZN to USD for Baku), with a swap button, every available currency, and a validated amount. It shows the result, the rate, the date of the rate and its source (ECB, or CBAR for AZN). The server page passes the latest rows of `fct_exchange_rate` (the conversion matrix of the newest date) to the browser as plain numbers: no API call or key in the browser.
- **City photos:** city cards (home, explore) and the city page header show the city's photo from `api.city_images`. `next/image` reserves the space and picks the right size, but the browser loads Wikimedia's own thumbnails (330, 500, 960 or 1280 px wide) directly through a custom loader that accepts only `upload.wikimedia.org/wikipedia/commons/` URLs: the server-side optimiser fetched as `node` and Wikimedia answered with 429 ([ADR-0011](docs/adr/0011-city-images-from-wikimedia-commons-via-wikidata.md)). Copies in our own storage with full optimisation are planned for phase 2, when galleries arrive. Credits are always shown: author, licence (linked) and "via Wikimedia Commons" (linked to the file page). Without a photo, the neutral placeholder is shown.
- **Climate chart:** a hand-built SVG rendered on the server (no chart library, no JavaScript): average high and low temperature per month as lines and rainy days as bars, with the best months highlighted. A compact layout replaces the wide one on small screens, and the same numbers are available as a table ("Show the data as a table"). Colours meet 3:1 contrast in the light and dark themes.

**Data flow:** gold tables (lakehouse) -> `publish_cloud` -> Supabase schema `api` (read-only REST API, [ADR-0010](docs/adr/0010-serve-site-from-supabase-rest-with-read-only-rls.md)) -> Next.js on Vercel. The data layer in [`web/lib/data/`](web/lib/data/) reads Supabase **on the server only** (`server-only`; `SUPABASE_URL` and `SUPABASE_ANON_KEY`, not `NEXT_PUBLIC_*`), so the key never reaches browsers.

- **Rendering:** every page is prerendered at build time, including the 10 city pages, and the data behind them is refreshed in the background at most once an hour (incremental static regeneration through `"use cache"` and the `cacheLife` profile `data` in `next.config.ts`). A visit never waits for Supabase; a page is at most about an hour behind a publish.
- **When Supabase is down:** cached pages keep serving their last good version (up to 7 days); a page with nothing cached shows "Data temporarily unavailable" instead of failing; a build without data fails on purpose, so Vercel keeps the previous deployment.
- **Fixture mode:** `DATA_SOURCE=fixture` uses a small built-in sample (3 cities, [`web/lib/data/fixtures.ts`](web/lib/data/fixtures.ts)) instead of Supabase. CI builds and tests this way, without secrets.
- **Tests:** Vitest unit tests for the data helpers, the conversion math (including AZN pairs and inverse consistency) and the chart scales; Playwright end-to-end tests ([`web/e2e/`](web/e2e/)) for the home page, the explore filters, the converter, the climate chart and the 404 page, run by the CI job "Web e2e" against a fixture build (Chromium only, browsers cached).

Run it locally (Node.js 26; the local data stack is not needed, keep it stopped):

```bash
make web-install   # npm ci in web/
make web-env       # write web/.env.local (Supabase URL and anon key) from the root .env
make web-dev       # http://localhost:3000, real data
make web-lint      # ESLint
make web-test      # unit tests (Vitest): data helpers and fixtures
make web-e2e       # optional: end-to-end tests (Playwright, Chromium) on a fixture build, in the official Playwright image
make web-build     # production build (real data; DATA_SOURCE=fixture to build without Supabase)
```

How deploys work:

- **Push or merge to `main`:** production deployment at https://wanderlake.vercel.app.
- **Pull requests and other branches:** a preview deployment; the Vercel bot posts the link on the PR. Previews require a Vercel login (Standard Protection), production is public.
- **Environment variables:** `SUPABASE_URL` and `SUPABASE_ANON_KEY` for production and preview come from OpenTofu (`vercel_project_environment_variable`). The key is write-only there: it is sent to Vercel but never stored in OpenTofu state. After rotating it, bump `supabase_anon_key_version` and run `make tofu-plan` / `make tofu-apply`.
- **Only when `web/` changes:** an ignored build step skips Vercel builds for commits that do not touch `web/`.
- The footer shows the deployed commit (`VERCEL_GIT_COMMIT_SHA`), or `local` outside Vercel.

## Attribution

- **Open-Meteo:** weather data by Open-Meteo.com (https://open-meteo.com/), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); history is based on ERA5/ERA5-Land reanalysis and ECMWF IFS data. The free API is used under its non-commercial terms. Wanderlake stores responses unchanged in raw and only types and reshapes them (one row per city and day) in bronze.
- **FCDO travel advice:** contains public sector information licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) (UK Foreign, Commonwealth & Development Office travel advice from GOV.UK). Stored unchanged in raw and bronze; any cleaned or summarised text in later layers is derived by Wanderlake.
- **GeoNames:** city data (c) GeoNames (https://www.geonames.org), licensed under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/) (CC BY 4.0). The data is provided "as is" without warranty. Wanderlake stores it unchanged in raw and loads it, typed, into bronze; no other changes are made at that layer.

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.

| Command | What it does |
|---|---|
| `make setup` | Installs the Python dev tools (ruff, sqlfluff, pre-commit, pytest) with uv and installs the git pre-commit hooks |
| `make lint` | Runs every pre-commit hook on all files: whitespace and file checks, ruff, sqlfluff and gitleaks |
| `make format` | Formats Python code and applies safe lint fixes with ruff |
| `make test` | Runs the pytest suite (DAG integrity tests are skipped, since Airflow is not installed locally) |
| `make test-dags` | Runs the DAG integrity tests inside the running Airflow container (needs `make up`) |

Run `make` on its own to list all targets. The hooks also run automatically on every `git commit`.

CI (GitHub Actions) runs the same checks on every pull request and push to `main`: lint, unit tests, and DAG integrity against Airflow 3.4.0. Dependabot opens weekly grouped updates for GitHub Actions and Python dependencies.

## Repository layout

| Path | Purpose |
|---|---|
| `infra/compose` | Docker Compose stack for local development |
| `infra/airflow` | Custom Airflow image (Dockerfile, pinned requirements) |
| `infra/tofu` | OpenTofu infrastructure as code for cloud resources |
| `infra/prod` | Production pipeline: prod Trino settings, one-time catalog and GitHub setup |
| `.github/workflows` | CI and the production pipeline |
| `ingestion` | Python ingestion package and its tests |
| `airflow/dags` | Airflow DAGs |
| `streaming` | Kafka and Debezium configuration and consumers |
| `spark/jobs` | Spark jobs writing Iceberg tables |
| `config` | Shared configuration (`cities.yaml`) |
| `dbt` | dbt project for silver and gold models (see Transformations) |
| `legacy` | Simulated legacy/operational source systems |
| `ai` | RAG pipeline, embeddings and MCP server |
| `api` | FastAPI backend |
| `web` | Next.js website |
| `dashboards` | Evidence.dev and Superset assets |
| `observability` | Monitoring, logging and alerting configuration |
| `tests` | Cross-component and end-to-end tests |
| `docs/adr` | Architecture decision records ([index](docs/adr/README.md)) |
| `docs/runbooks` | Operational runbooks |

## License

MIT. See [LICENSE](LICENSE).
