# ADR-0004: Hybrid run model: full stack on the laptop, always-on version on free cloud tiers

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

There is no dedicated server. The only machine is a personal laptop with 8 GB RAM (WSL is limited to
4 GB; `free -h` inside WSL shows 3.8 GiB) and a 128 GB SSD. A work laptop exists but is deliberately
not used for this project.

The full architecture (Kafka, Debezium, Spark, Iceberg, Trino, Airflow, Postgres, AI services) does
not fit in 4 GB at once. The `core` profile alone has memory limits of 512 MB (Postgres) + 640 MB
(SeaweedFS) + 2 GB (Airflow); with it running, WSL had 1.3 GiB available.

The project should still have a public, always-on version, at no cost.

## Decision

Split the system into a local stack and an always-on cloud version:

- **Laptop:** Docker Compose with profiles (`make up PROFILE=...`). Each profile is a group of
  services that fits the memory budget; `core` is Postgres + pgvector, SeaweedFS and Airflow.
- **Cloud (free tiers):** Cloudflare R2 for object storage, Supabase for Postgres, Vercel for the
  website, and GitHub Actions for CI and for scheduled light jobs. R2, Supabase and Vercel are
  managed with OpenTofu (see [ADR-0005](0005-use-opentofu.md)).
- **Heavy services (Kafka, Spark, Trino):** run in GitHub Codespaces until the laptop gets more RAM.
- **Same code in both:** configuration comes from environment variables (`S3_*`, Postgres settings),
  not from code paths per environment.

## Alternatives considered

- **Everything on the laptop:** does not fit in 4 GB.
- **A paid VPS or cloud VM:** would run everything, but the project's target cost is $0.
- **Using the work laptop:** ruled out to keep personal and employer resources separate.
- **Cloud only:** free tiers cannot run Kafka, Spark or Airflow continuously.

## Consequences

- Cost is $0, but every cloud part lives within free-tier limits (for example 10 GB on R2, a 500 MB
  database on Supabase, and Hobby-plan terms on Vercel; see the cloud runbook).
- The free Supabase project pauses after 7 days without activity, so the always-on version needs
  regular activity or a manual resume.
- Real-time parts (Kafka, Debezium, streaming) are not always on. They are shown in the demo video
  and can be run on demand.
- Two environments must stay compatible. Generic variables and shared code keep the gap small, but
  each new component needs a local and a cloud answer.
- Revisit this ADR after a RAM upgrade.
