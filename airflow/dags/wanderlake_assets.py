"""Airflow Assets for Wanderlake's Iceberg tables: one definition, shared by producer and consumer.

An ingestion DAG's load task declares its bronze table as an outlet; a successful run of that
task records an asset event, and wanderlake_transform is scheduled on those events. The URI names
the table (iceberg://<catalog>/<schema>/<table>); the asset name is the table as Trino shows it.
This module defines no DAG (it is listed in .airflowignore); the DAGs folder is on PYTHONPATH
(compose, CI).
"""

from __future__ import annotations

from airflow.sdk import Asset

CATALOG = "wanderlake"


def iceberg_asset(schema: str, table: str) -> Asset:
    return Asset(name=f"{schema}.{table}", uri=f"iceberg://{CATALOG}/{schema}/{table}")


# Bronze: written by the ingestion DAGs.
FRANKFURTER_RATES = iceberg_asset("bronze", "frankfurter_rates")
CBAR_RATES = iceberg_asset("bronze", "cbar_rates")
OPEN_METEO_FORECAST = iceberg_asset("bronze", "open_meteo_forecast")
OPEN_METEO_HISTORY = iceberg_asset("bronze", "open_meteo_history")
FCDO_ADVICE = iceberg_asset("bronze", "fcdo_advice")
REST_COUNTRIES = iceberg_asset("bronze", "rest_countries")
GEONAMES_CITIES = iceberg_asset("bronze", "geonames_cities")
CONFIG_CITIES = iceberg_asset("bronze", "config_cities")

# The daily inputs that trigger wanderlake_transform (all four must have new events).
DAILY_TRANSFORM_INPUTS = (FRANKFURTER_RATES, CBAR_RATES, OPEN_METEO_FORECAST, OPEN_METEO_HISTORY)

# Gold: one asset for the whole layer, updated when a wanderlake_transform run completes.
GOLD = Asset(name="gold", uri=f"iceberg://{CATALOG}/gold")

# Supabase schema api (the website's read-only REST API), updated when publish_cloud verified
# every table; for later consumers such as a website rebuild.
SUPABASE_API = Asset(name="supabase.api", uri="supabase://wanderlake/api")
