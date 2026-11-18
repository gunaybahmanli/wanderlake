# ADR-0003: Use Apache Iceberg with a SQL catalog in Postgres

- **Status:** Accepted (implementation in Phase 1)
- **Date:** 2026-11-18

## Context

The bronze layer currently stores raw JSON files in the `raw` bucket. From Phase 1, silver and gold
data will be tables written by Spark, queried by Trino, and modelled with dbt. These tables need
schema evolution, safe concurrent writes and time travel, and every engine must agree on which files
make up a table. That requires a table format and a catalog.

Constraints: the catalog must work locally (laptop, tight memory) and in the always-on cloud
version, and it must not add a service to run. Postgres already exists in both places: the local
`postgres` container, and the Supabase project in the cloud.

## Decision

Use Apache Iceberg as the table format, with Iceberg's SQL (JDBC) catalog stored in Postgres:

- Locally, the catalog tables live in the existing Postgres (a dedicated schema in the `wanderlake`
  database) and the warehouse is the `lakehouse` bucket on SeaweedFS.
- In the cloud, the same catalog lives in the Supabase Postgres and the warehouse is the R2 bucket
  `wanderlake-lake`.
- Spark, Trino and PyIceberg all connect to the same catalog type, configured from environment
  variables.

Nothing in the repository implements this yet; it is the design that Phase 1 builds.

## Alternatives considered

- **Hive Metastore:** a separate Java service with its own database, and memory the laptop does not
  have to spare.
- **Project Nessie:** adds Git-like branching of the catalog, but is another service to run, and
  branching is not needed yet.
- **A REST catalog (Lakekeeper, Apache Polaris, or a managed one):** the direction the Iceberg
  ecosystem is moving, with features such as credential vending. It is still one more service
  locally, and a managed option would make the local and cloud setups differ.
- **Plain Parquet files without a table format:** no atomic commits, schema evolution or time
  travel, and every engine would need its own idea of which files belong to a table.

## Consequences

- No catalog service to run or pay for: the catalog is a few tables in a database that already exists.
- Commits are as reliable as Postgres transactions.
- The catalog depends on Postgres being reachable. In the cloud, the free Supabase project pauses
  after 7 days without activity, which also makes the cloud catalog unavailable until it is resumed.
- If REST catalog features become necessary (credential vending, multi-tenant access), moving to a
  REST catalog is a catalog migration, recorded in a new ADR that supersedes this one.
