# Architecture Decision Records

Each ADR records one significant decision: its context, the decision, the alternatives considered
and the consequences.

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-use-apache-airflow-3.md) | Use Apache Airflow 3 for orchestration | Accepted | 2026-11-18 |
| [0002](0002-use-seaweedfs-for-local-object-storage.md) | Use SeaweedFS as the local S3-compatible object store | Accepted | 2026-11-18 |
| [0003](0003-use-iceberg-with-sql-catalog.md) | Use Apache Iceberg with a SQL catalog in Postgres | Accepted, implemented in Phase 1 step 2 | 2026-11-18 |
| [0004](0004-hybrid-run-model.md) | Hybrid run model: full stack on the laptop, always-on version on free cloud tiers | Accepted | 2026-11-18 |
| [0005](0005-use-opentofu.md) | Use OpenTofu for infrastructure as code | Accepted | 2026-11-18 |
| [0006](0006-accept-unsigned-opentofu-providers.md) | Accept unsigned OpenTofu providers (Supabase, Vercel) | Accepted | 2026-11-18 |
| [0007](0007-custom-airflow-image.md) | Custom Airflow image with dbt in a separate virtualenv | Accepted | 2026-11-19 |

## Adding an ADR

Copy [`0000-template.md`](0000-template.md) to the next free number with a short kebab-case name
(for example `0007-use-trino-as-query-engine.md`), fill it in with status Proposed, and add it to
this table in the same pull request; set it to Accepted when the pull request is merged.
ADRs are never deleted: a decision that changes gets a new ADR, and the old one is marked
"Superseded by ADR-XXXX".
