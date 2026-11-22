-- Schema "api": the gold tables published for the public website (Supabase REST API).
-- Written by the publish job (ingestion.publish) as the postgres user; read by the anon role
-- (row-level security and grants: next migration). Column names and types follow the dbt gold
-- contracts (dbt/models/gold/_gold.yml); tests/publish checks they stay in sync.
-- Idempotent: safe to run again.

create schema if not exists api;
comment on schema api is 'Wanderlake gold tables, published read-only for the public website.';

-- Tables created here get no automatic privileges for the API roles; grants are explicit.
alter default privileges in schema api revoke all on tables from anon, authenticated;

create table if not exists api.dim_country (
  cca2 text primary key,
  cca3 text,
  name_common text,
  name_official text,
  capital text,
  region text,
  subregion text,
  population bigint,
  area_km2 double precision,
  lat double precision,
  lon double precision,
  flag_png_url text,
  independent boolean,
  un_member boolean,
  primary_currency_code text,
  primary_currency_name text,
  primary_currency_symbol text,
  currency_codes text[],
  language_names text[],
  has_configured_city boolean not null,
  _built_at timestamptz
);

create table if not exists api.dim_city (
  city_id text primary key,
  name text,
  country_code text not null,
  country_name text,
  region text,
  subregion text,
  flag_png_url text,
  language_names text[],
  timezone text,
  latitude double precision,
  longitude double precision,
  currency text,
  primary_currency_name text,
  primary_currency_symbol text,
  rate_source text,
  inside_airbnb boolean,
  geonames_id bigint,
  geonames_population bigint,
  elevation_m integer,
  elevation_source text,
  admin1_code text,
  _built_at timestamptz
);

create table if not exists api.city_overview (
  city_id text primary key,
  name text,
  country_code text,
  country_name text,
  region text,
  flag_png_url text,
  timezone text,
  latitude double precision,
  longitude double precision,
  currency text,
  primary_currency_name text,
  primary_currency_symbol text,
  language_names text[],
  geonames_population bigint,
  elevation_m integer,
  rate_to_eur numeric(28, 12) not null,
  rate_to_usd numeric(28, 12) not null,
  rate_to_azn numeric(28, 12) not null,
  forecast_date date,
  today_weather_description text,
  today_temp_max_c double precision,
  today_temp_min_c double precision,
  today_precipitation_probability double precision,
  has_travel_alert boolean,
  advice_updated_at timestamptz,
  best_months text[],
  _built_at timestamptz
);

-- The last 400 days only (the publish job filters; the lakehouse keeps the full history).
create table if not exists api.fct_exchange_rate (
  date date not null,
  from_currency text not null,
  to_currency text not null,
  rate numeric(28, 12) not null,
  from_rate_date date not null,
  to_rate_date date not null,
  source_from text,
  source_to text,
  _built_at timestamptz,
  primary key (date, from_currency, to_currency)
);

create table if not exists api.fct_weather_daily (
  city_id text not null,
  weather_date date not null,
  year integer,
  month integer,
  is_weekend boolean,
  temp_max_c double precision,
  temp_min_c double precision,
  temp_mean_c double precision,
  temp_range_c double precision,
  precipitation_mm double precision,
  is_rainy_day boolean,
  sunshine_hours double precision,
  wind_max_kmh double precision,
  weather_code integer,
  weather_description text,
  _built_at timestamptz,
  primary key (city_id, weather_date)
);

create table if not exists api.agg_city_climate_monthly (
  city_id text not null,
  month integer not null,
  years_covered integer,
  days_covered integer,
  avg_temp_max_c double precision,
  avg_temp_min_c double precision,
  avg_rainy_days double precision,
  avg_sunshine_hours_per_day double precision,
  pleasant_day_share double precision,
  _built_at timestamptz,
  primary key (city_id, month)
);

create table if not exists api.weather_forecast_latest (
  city_id text not null,
  forecast_date date not null,
  run_date date not null,
  weather_code integer,
  weather_description text,
  temp_max_c double precision,
  temp_min_c double precision,
  temp_mean_c double precision,
  precipitation_mm double precision,
  precipitation_probability double precision,
  snowfall_cm double precision,
  wind_max_kmh double precision,
  sunshine_hours double precision,
  uv_index_max double precision,
  _built_at timestamptz,
  primary key (city_id, forecast_date)
);

create table if not exists api.travel_advice_current (
  country_code text primary key,
  title text,
  has_alert boolean not null,
  alert_statuses text[],
  public_updated_at timestamptz not null,
  change_description text,
  summary_text text,
  safety_text text,
  entry_requirements_text text,
  health_text text,
  last_seen_snapshot_date date,
  _built_at timestamptz
);
