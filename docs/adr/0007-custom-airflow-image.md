# ADR-0007: Custom Airflow image with dbt in a separate virtualenv

- **Status:** Accepted
- **Date:** 2026-11-19

## Context

Until now Airflow ran on the unmodified official image (`apache/airflow:3.4.0-python3.12`). The
next Phase 1 steps need three more libraries inside Airflow:

- **PyIceberg**, to read and write Iceberg tables through the JDBC catalog
  ([ADR-0003](0003-use-iceberg-with-sql-catalog.md)).
- **Astronomer Cosmos**, to run dbt projects as Airflow task groups.
- **dbt-core with dbt-trino**, the transformation tool Cosmos calls.

Airflow is installed with an official constraints file that pins every dependency. PyIceberg is in
that file (0.12.0) and its needs are already met (SQLAlchemy 2.0, PyArrow, psycopg2). Cosmos 1.16.0
supports Airflow 3.4 and all its dependencies are pinned there too. dbt is different: it brings its
own pins (for example protobuf, networkx, mashumaro and its own pydantic range), and a future
release can require versions that Airflow's constraints forbid.

## Decision

Build our own image, `wanderlake-airflow:local`, from `infra/airflow/Dockerfile`:

- `FROM` the same official image, pinned by tag **and** digest.
- Install `requirements.txt` (PyIceberg with the `sql-postgres` and `pyarrow` extras, and Cosmos,
  exact pins) into the main environment **with the official constraints file**. The constraints
  URL is derived from the image's own Airflow and Python versions at build time, and `pip check`
  runs in the build.
- Install dbt into a **separate virtualenv**, `/opt/dbt-venv`, from `requirements-dbt.txt`, which
  pins the whole dbt dependency tree (64 packages), wheels only. Cosmos calls
  `/opt/dbt-venv/bin/dbt`, so dbt's dependencies never meet Airflow's. One exception to "wheels
  only": `dbt-core-experimental-parser` is published on PyPI only as a source package. Its build
  step compiles nothing; it downloads the prebuilt wheel for the platform from the `dbt-labs/dbt`
  GitHub release and verifies it against a sha256 stored in the package.
- No build tools, no pip cache; the final user is `airflow`. OCI labels name the source repository.
- Compose builds the image (`make build`); CI builds it with Buildx and the GitHub Actions cache,
  then checks the imports, `dbt --version` and `pip check`. The DAG integrity job installs the same
  `requirements.txt` with the same constraints, so DAG tests import what the image contains.
- Dependabot watches the base image (`docker` ecosystem for `infra/airflow`).

## Alternatives considered

- **`_PIP_ADDITIONAL_REQUIREMENTS` at container start:** no image to build, but it installs on
  every start (slow, needs the internet, and can resolve differently each time). The Airflow docs
  describe it as a quick-test option, not for anything that must be reproducible.
- **dbt in the same environment as Airflow:** simplest, but dbt and Airflow pin overlapping
  libraries; every upgrade of either becomes a dependency negotiation, and a conflict would break
  Airflow itself.
- **A separate dbt container (DockerOperator or a dbt service):** the strongest isolation, but it
  needs access to the Docker socket or a second long-running service, more memory on a 4 GB
  machine, and a more complex Cosmos setup.

## Consequences

- Reproducible: the same Dockerfile, pins and constraints give the same image locally and in CI.
- dbt can be upgraded independently of Airflow, and vice versa.
- One more step: after changing `infra/airflow/`, run `make build`. The first build downloads the
  dbt dependencies; later builds reuse the layer cache.
- The image is larger than the official one (it adds a second Python environment).
- When Dependabot bumps the base image to a new Airflow version, `AIRFLOW_VERSION` in
  `.github/workflows/ci.yml` must be updated in the same PR (a comment there says so), and the
  PyIceberg pin may need to follow the new constraints file; `pip check` and the CI image job
  will fail if it does not.
