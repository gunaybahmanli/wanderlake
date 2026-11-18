# Wanderlake

Wanderlake is a global travel intelligence platform built on a self-made, fully open-source lakehouse. It pulls travel data from many sources (flights, weather, exchange rates, destinations, events and more) through batch and streaming pipelines, lands it in Apache Iceberg tables on object storage, models it into clean bronze, silver and gold layers, and serves it through an AI assistant, a public API, a website and analytics dashboards. The whole stack runs locally in Docker on WSL2, and an always-on version is published on free cloud tiers. It is built like a production system: infrastructure as code, CI/CD, data quality tests, observability, architecture decision records and runbooks.

## What it does

- Ingests travel-related data from public APIs, files and operational databases on a schedule (Airflow) and in real time (Kafka + Debezium CDC).
- Stores everything in an open lakehouse: Apache Iceberg tables on S3-compatible storage (MinIO locally, Cloudflare R2 in the cloud), queried with Trino and Spark.
- Transforms raw data into tested, documented bronze, silver and gold models with dbt.
- Answers natural-language travel questions through an AI layer (Ollama, RAG over pgvector, and an MCP server exposing lakehouse tools).
- Serves insights through a FastAPI backend, a Next.js website, and Evidence.dev / Superset dashboards.

## Architecture

```
  Sources                 Ingestion                  Lakehouse (Apache Iceberg on MinIO / R2)        Consumers
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
| Object storage | MinIO (local), Cloudflare R2 (cloud) |
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

- **Phase 0: Foundation.** Repository structure, Docker Compose base, MinIO, Postgres, CI skeleton, ADRs.
- **Phase 1: Batch lakehouse.** Ingestion package, Airflow DAGs, Spark jobs, Iceberg bronze/silver/gold, Trino, dbt models and tests.
- **Phase 2: Streaming and CDC.** Kafka, Debezium, streaming ingestion into Iceberg.
- **Phase 3: AI layer.** pgvector embeddings, Ollama, RAG pipeline, MCP server.
- **Phase 4: Products.** FastAPI backend, Next.js website, Evidence.dev and Superset dashboards.
- **Phase 5: Cloud and production hardening.** Cloudflare R2, Supabase, Vercel, GitHub Actions deployments, OpenTofu, observability.

## Run locally

Coming soon. Instructions will be added once the Phase 0 Docker Compose stack is in place.

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
