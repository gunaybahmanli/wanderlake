-- Read-only public access to schema "api" (ADR-0010): the website calls the Supabase REST API
-- with the anon key, which may SELECT and nothing else.
--   * usage on the schema, SELECT on each table, no INSERT/UPDATE/DELETE/TRUNCATE
--   * row-level security on every table, with one SELECT policy for anon (all rows are public)
-- Without a policy for a command, RLS denies it; without the grant, Postgres denies it first.
-- The role "authenticated" gets nothing: the site has no sign-in.
-- Idempotent: safe to run again.

revoke all on schema api from public, anon, authenticated;
grant usage on schema api to anon;

do $$
declare
  t text;
begin
  foreach t in array array[
    'dim_country', 'dim_city', 'city_overview', 'fct_exchange_rate', 'fct_weather_daily',
    'agg_city_climate_monthly', 'weather_forecast_latest', 'travel_advice_current'
  ] loop
    execute format('alter table api.%I enable row level security', t);
    execute format('revoke all on table api.%I from public, anon, authenticated', t);
    execute format('grant select on table api.%I to anon', t);
    execute format('drop policy if exists anon_read on api.%I', t);
    execute format(
      'create policy anon_read on api.%I as permissive for select to anon using (true)', t
    );
  end loop;
end
$$;
