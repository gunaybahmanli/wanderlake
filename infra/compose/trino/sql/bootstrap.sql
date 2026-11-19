-- Lakehouse layers as Iceberg schemas (idempotent). Run with: make lakehouse-init
-- The parser check is switched off for these lines: sqlfluff's trino dialect cannot parse
-- CREATE SCHEMA ... WITH (...) yet. All other rules still apply.
-- noqa: disable=PRS
create schema if not exists iceberg.bronze with (location = 's3://lakehouse/warehouse/bronze/');
create schema if not exists iceberg.silver with (location = 's3://lakehouse/warehouse/silver/');
create schema if not exists iceberg.gold with (location = 's3://lakehouse/warehouse/gold/');
-- noqa: enable=PRS
show schemas from iceberg;
