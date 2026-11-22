# ADR-0010: Serve the public site from Supabase's REST API with read-only RLS (no custom backend)

- **Status:** Accepted
- **Date:** 2026-11-22

## Context

The lakehouse (Iceberg on SeaweedFS, Trino, Airflow) runs on a laptop, in Docker, only when
started. The public website (Next.js on Vercel, ADR-0004's hybrid model) must work around the
clock, so it cannot read from the laptop. It needs a small, always-on copy of the gold tables:
about 16,000 rows in 8 tables today (`dim_city`, `dim_country`, `city_overview`,
`fct_exchange_rate` for the last 400 days, `fct_weather_daily`, `agg_city_climate_monthly`,
`weather_forecast_latest`, `travel_advice_current`), refreshed once a day after
`wanderlake_transform`.

Constraints: free tiers only, no server for one person to patch and monitor, data that is
public anyway (no user accounts, nothing personal), and reads that can be filtered per city
or date range so the site does not download whole tables.

## Decision

- The DAG `publish_cloud` (scheduled on the `gold` asset) copies those tables into the schema
  **`api`** of the Supabase Postgres project (OpenTofu, ADR-0005), and the website reads them
  through **Supabase's built-in REST API (PostgREST) with the anon key**. There is no custom
  backend.
- **Read-only by construction**, in versioned SQL migrations (`supabase/migrations/`, applied
  by the publish job before loading):
  - `grant usage on schema api to anon` and `grant select` on each table; no insert, update,
    delete or truncate grants; the role `authenticated` gets nothing.
  - Row-level security enabled on every table, with one policy, `anon_read`:
    `for select to anon using (true)`. A write needs both a grant and a policy, and has neither.
  - The migration history lives in `wl_meta`, a schema the API does not expose.
- **Exposure:** `supabase_settings` in OpenTofu sets PostgREST's `db_schema` to
  `public,graphql_public,api`; `public` stays empty of our data.
- **Loading:** each table is replaced in one transaction (COPY into a temporary staging table,
  then delete and insert), so the API never serves a partial table, and the table, its policies
  and its grants are never dropped. Row counts are verified against the lakehouse after every
  publish. The same tables also go to R2 as Parquet (`gold/<table>/snapshot_date=.../` and
  `latest/`) for bulk and analytical use.
- `make api-check` proves the access rules against the live API: anon SELECT succeeds; INSERT,
  UPDATE and DELETE are rejected; `wl_meta` is not reachable.

## Alternatives considered

- **FastAPI on a free host** (Render, Fly.io, Railway): full control over endpoints and
  caching, and the `api/` folder is reserved for Phase 2. But it is a second always-on service
  to deploy, patch, secure and monitor; free hosts sleep after inactivity (cold starts of tens
  of seconds) or need a card; and for plain table reads it would re-implement what PostgREST
  already does (filters, ordering, paging, OpenAPI). It stays the plan for logic that SQL
  cannot express (Phase 2 AI endpoints), next to this API, not instead of it.
- **Direct database access from the site** (a Postgres connection string in Vercel functions):
  no extra layer, but it puts database credentials in the web tier, needs connection pooling
  from serverless functions, and moves access control from the database into application
  code, where a bug exposes more than one table. The browser could never use it directly.
- **Static JSON files only** (written to R2 or the Vercel build): the cheapest and fastest to
  serve, nothing to secure but a bucket. But every query shape must be precomputed (per-city
  files, per-range files), daily weather history and the rate matrix become large downloads or
  many files, and changing what the site needs means changing the publish job. The R2 Parquet
  snapshots keep this option open for bulk data.

## Consequences

- No backend code or server: the site calls `https://<ref>.supabase.co/rest/v1/<table>` with
  `Accept-Profile: api` and the anon key. The anon key is public by design; security rests on
  the grants and RLS in the migrations, which tests check (every published table has RLS, a
  SELECT-only anon policy, and no write grants) and `make api-check` verifies live.
- **Free-plan limits:** the project pauses after 7 days without activity (the daily publish and
  site traffic keep it active; a paused project fails the publish loudly); 500 MB database
  (today a few MB); PostgREST returns at most 1,000 rows per request (`max_rows`), so the site
  pages or filters `fct_weather_daily` and `fct_exchange_rate`.
- **The API is coupled to the gold contracts:** `api` columns mirror the dbt contracts, a test
  compares the migrations with `_gold.yml`, and the publish job refuses a table whose columns
  differ. Changing a published gold column therefore needs a new Supabase migration in the same
  change.
- The publish job writes as the `postgres` user, because it also runs the migrations. A
  dedicated role that can only write to `api` (and a separate migration step) is a follow-up if
  the job ever runs outside this machine.
- Supabase is not the system of record: the lakehouse is. `api` can be dropped and rebuilt by
  one `make publish`.
