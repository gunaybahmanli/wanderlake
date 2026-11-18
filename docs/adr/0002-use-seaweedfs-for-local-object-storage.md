# ADR-0002: Use SeaweedFS as the local S3-compatible object store

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

The lakehouse needs S3-compatible object storage locally, with the same client code working
against Cloudflare R2 in the cloud. The original plan was MinIO. When the `core` profile was built,
the checks showed:

- `minio/minio` and `minio/mc` on Docker Hub returned "object not found".
- Pinned release tags (for example `RELEASE.2025-09-07T16-13-09Z`) could not be pulled from Docker
  Hub or from Quay (`quay.io/minio/minio`).
- `bitnami/minio` on Docker Hub had no tags.
- The `minio/minio` GitHub repository is archived.

Building MinIO from the archived source would work, but would never receive security fixes.

## Decision

Use SeaweedFS (`chrislusf/seaweedfs:4.54`, Apache-2.0) as a single container:

- `weed server -s3` runs master, volume, filer and S3 gateway in one process, with a `mem_limit`
  of 640 MB. Measured with `docker stats`: 123.6 MiB.
- S3 credentials come from `.env` (`S3_ACCESS_KEY`, `S3_SECRET_KEY`); `infra/compose/seaweedfs/start.sh`
  renders them into the SeaweedFS identity config at startup.
- Path-style addressing (no `-s3.domainName`).
- The one-shot `s3-init` container (AWS CLI) creates the `raw` and `lakehouse` buckets idempotently.
- All code uses generic `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` and `S3_REGION` variables,
  so switching to R2 is a configuration change, not a code change.

SeaweedFS releases about weekly (median gap of 8 days between the last 12 releases).

## Alternatives considered

- **Garage:** S3-compatible and very light, but AGPL-3.0, needs a cluster layout step after
  startup, generates its own keys (they must be imported to match `.env`), and has a smaller
  community.
- **MinIO built from the archived source:** familiar, but no security fixes from now on.
- **No local store (use R2 directly):** would make local development depend on the internet and on
  free-tier limits.

## Consequences

- An actively maintained, permissively licensed store that fits the memory budget.
- There is no MinIO-style console. Objects are inspected with `make s3-ls` or the AWS CLI (see the
  README), and SeaweedFS's read-only filer (port 8888) and master (port 9333) UIs.
- SeaweedFS behaves like S3 for the operations used so far (bucket create, put, head, get, list).
  Iceberg and Spark workloads in Phase 1 will be the real compatibility test.
- The generic `S3_*` variables keep local and cloud code identical.
