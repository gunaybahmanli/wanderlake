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

2. Start the core services (Postgres + pgvector, SeaweedFS, Airflow):

   ```bash
   make up            # same as: make up PROFILE=core
   make ps            # wait until every service is "healthy" (Airflow takes 1-2 minutes)
   ```

3. Open the services:

   | Service | URL / port | Login |
   |---|---|---|
   | Airflow UI | http://localhost:8080 | `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` from `.env` |
   | S3 API (SeaweedFS) | http://localhost:8333 (path-style) | `S3_ACCESS_KEY` / `S3_SECRET_KEY` from `.env` |
   | SeaweedFS filer UI | http://localhost:8888/buckets/ | none (read-only file browser, local only) |
   | SeaweedFS master UI | http://localhost:9333 | none (cluster status) |
   | Postgres | `localhost:5432`, database `wanderlake` | `POSTGRES_USER` / `POSTGRES_PASSWORD` from `.env` |

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

   To also delete all data, run `docker compose --env-file .env -f infra/compose/compose.yaml --profile core down -v`.

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

## Pipelines

| DAG | Schedule (UTC) | Source | Output (bronze) |
|---|---|---|---|
| `frankfurter_rates` | daily 16:30 | [Frankfurter API](https://frankfurter.dev), ECB reference rates, base EUR | `s3://raw/frankfurter/rates/date=YYYY-MM-DD/rates.json` |

### `frankfurter_rates`

Fetches the European Central Bank reference rates from `https://api.frankfurter.dev/v2/providers/ecb/rates?date=<date>&base=EUR` and stores the response body unchanged. The date is the run's logical date, and re-running a date overwrites the same object.

- On weekends, holidays, or before the ECB publishes, the API returns the last business day. The file is still stored, and the object metadata `rate_date` shows which day the rates are for.
- Each object carries metadata: `ingested_at`, `source_url`, `http_status`, `requested_date`, `rate_date`.
- The code lives in the `ingestion` package (`ingestion/src/ingestion/sources/frankfurter.py`); the DAG only calls it.

Run and inspect it:

```bash
make backfill DAG=frankfurter_rates START=2026-11-11 END=2026-11-17   # one run per day, inclusive
make s3-ls                                                            # list objects in the raw bucket
make s3-ls BUCKET=lakehouse                                           # or another bucket
```

New DAGs start paused: unpause `frankfurter_rates` in the Airflow UI (or `docker exec wanderlake-airflow-1 airflow dags unpause frankfurter_rates`) for the daily schedule to run. You can trigger a single run from the UI with the play button.

## Website

The website is a Next.js app (App Router, TypeScript) in [`web/`](web/), deployed on Vercel. The Vercel project itself is managed by OpenTofu in `infra/tofu`.

Run it locally (Node.js 26):

```bash
make web-install   # npm ci in web/
make web-dev       # http://localhost:3000
make web-lint      # ESLint
make web-build     # production build
```

How deploys work:

- **Push or merge to `main`:** production deployment at https://wanderlake.vercel.app.
- **Pull requests and other branches:** a preview deployment; the Vercel bot posts the link on the PR. Previews require a Vercel login (Standard Protection), production is public.
- **Only when `web/` changes:** an ignored build step skips Vercel builds for commits that do not touch `web/`.
- The footer shows the deployed commit (`VERCEL_GIT_COMMIT_SHA`), or `local` outside Vercel.

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
| `infra/tofu` | OpenTofu infrastructure as code for cloud resources |
| `ingestion` | Python ingestion package and its tests |
| `airflow/dags` | Airflow DAGs |
| `streaming` | Kafka and Debezium configuration and consumers |
| `spark/jobs` | Spark jobs writing Iceberg tables |
| `dbt` | dbt project for silver and gold models |
| `legacy` | Simulated legacy/operational source systems |
| `ai` | RAG pipeline, embeddings and MCP server |
| `api` | FastAPI backend |
| `web` | Next.js website |
| `dashboards` | Evidence.dev and Superset assets |
| `observability` | Monitoring, logging and alerting configuration |
| `tests` | Cross-component and end-to-end tests |
| `docs/adr` | Architecture decision records |
| `docs/runbooks` | Operational runbooks |

## License

MIT. See [LICENSE](LICENSE).
