# Wanderlake Airflow image: the official image plus PyIceberg, Cosmos, and dbt in its own venv.
# See docs/adr/0007-custom-airflow-image.md. Build with: make build
FROM apache/airflow:3.4.0-python3.12@sha256:818f94affc5bd7ce0be0cc04f2ace09aa8382e65190a73a08881229ff32700cb

LABEL org.opencontainers.image.title="wanderlake-airflow" \
      org.opencontainers.image.description="Apache Airflow 3 for Wanderlake, with PyIceberg, Astronomer Cosmos and dbt-trino (separate venv)" \
      org.opencontainers.image.source="https://github.com/gunaybahmanli/wanderlake" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="docker.io/apache/airflow:3.4.0-python3.12"

# 1. Main Airflow environment, with the official constraints for this image's own Airflow and
#    Python versions (derived at build time, so a base image bump keeps them in sync).
COPY --chown=airflow:0 requirements.txt /tmp/requirements.txt
RUN AIRFLOW_VERSION="$(python -c 'import airflow; print(airflow.__version__)')" \
 && PYTHON_VERSION="$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" \
 && CONSTRAINTS="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
 && echo "Installing with ${CONSTRAINTS}" \
 && pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements.txt --constraint "${CONSTRAINTS}" \
 && pip check \
 && rm /tmp/requirements.txt

# 2. dbt in its own virtualenv, so dbt's pins can never conflict with Airflow's.
#    Cosmos calls /opt/dbt-venv/bin/dbt.
#    Wheels only, with one exception: dbt-core-experimental-parser is published on PyPI only as a
#    source package whose build step downloads the prebuilt wheel for this platform from the
#    dbt-labs/dbt GitHub release and checks it against a sha256 stored in the package. No compiler.
USER root
RUN mkdir -p /opt/dbt-venv && chown airflow:0 /opt/dbt-venv
USER airflow
COPY --chown=airflow:0 requirements-dbt.txt /tmp/requirements-dbt.txt
RUN python -m venv /opt/dbt-venv \
 && /opt/dbt-venv/bin/pip install --no-cache-dir \
      --only-binary=:all: --no-binary=dbt-core-experimental-parser -r /tmp/requirements-dbt.txt \
 && /opt/dbt-venv/bin/pip check \
 && /opt/dbt-venv/bin/dbt --version \
 && /opt/dbt-venv/bin/pip uninstall --yes pip \
 && rm /tmp/requirements-dbt.txt

ENV DBT_VENV=/opt/dbt-venv
