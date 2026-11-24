-- Run metadata of the production pipeline (ADR-0012).
--
-- ops.pipeline_runs: one row per GitHub Actions run attempt (status, timings, git commit, rows
-- published per table, what failed). Schema ops is private: not in the REST API's exposed
-- schemas, and public, anon and authenticated have no access at all. Only the publisher (the
-- database owner role) writes it.
--
-- api.last_updated: one row, the time of the last successful publish. The website reads it
-- (About page, footer). Read-only for anon, like every table in schema api (ADR-0010).
-- It moves only after a publish whose row counts all matched.
-- Idempotent: safe to run again.

create schema if not exists ops;
revoke all on schema ops from public, anon, authenticated;

create table if not exists ops.pipeline_runs (
  run_id text not null,
  run_attempt integer not null default 1,
  environment text not null,
  trigger text,
  status text not null check (status in ('running', 'succeeded', 'failed', 'cancelled')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  git_sha text,
  rows_published jsonb,
  failed_step text,
  error_summary text,
  run_url text,
  primary key (run_id, run_attempt)
);
revoke all on table ops.pipeline_runs from public, anon, authenticated;

create table if not exists api.last_updated (
  id boolean primary key default true check (id),
  published_at timestamptz not null,
  run_id text
);

alter table api.last_updated enable row level security;
revoke all on table api.last_updated from public, anon, authenticated;
grant select on table api.last_updated to anon;
drop policy if exists anon_read on api.last_updated;
create policy anon_read on api.last_updated as permissive for select to anon using (true);
