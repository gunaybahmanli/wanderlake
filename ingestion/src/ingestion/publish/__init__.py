"""Publish gold tables to the cloud: Supabase (schema api, read-only REST) and R2 (Parquet).

Modules:
    tables      which gold tables are published, and how (primary key, row filter)
    migrations  versioned SQL migrations for Supabase (supabase/migrations)
    supabase    connection, atomic table replace (COPY into staging, swap in one transaction)
    r2          Parquet snapshots on Cloudflare R2
    job         read gold via PyIceberg, publish each table, verify row counts
Run all of it: python -m ingestion.publish (make publish), or the publish_cloud DAG.
"""
