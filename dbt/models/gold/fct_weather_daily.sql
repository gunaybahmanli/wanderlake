{{
  config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['city_id', 'weather_date'],
    on_schema_change='fail'
  )
}}
-- Daily observed weather per city, shaped for charts. Incremental (Iceberg MERGE on
-- city_id + weather_date): a full build loads all history; later runs reprocess only the last
-- 7 days before the newest weather_date already loaded, because the Open-Meteo archive revises
-- its newest days (provisional model values, replaced by ERA5 about 5 days later).
-- Rebuild everything with: dbt build --full-refresh -s fct_weather_daily
select
  cast(w.city_id as varchar) as city_id,
  cast(w.weather_date as date) as weather_date,
  cast(year(w.weather_date) as integer) as year,  -- noqa: RF04
  cast(month(w.weather_date) as integer) as month,  -- noqa: RF04
  cast(day_of_week(w.weather_date) in (6, 7) as boolean) as is_weekend,
  cast(w.temperature_2m_max as double) as temp_max_c,
  cast(w.temperature_2m_min as double) as temp_min_c,
  cast(w.temperature_2m_mean as double) as temp_mean_c,
  cast(w.temperature_2m_max - w.temperature_2m_min as double) as temp_range_c,
  cast(w.precipitation_sum as double) as precipitation_mm,
  cast(w.precipitation_sum >= 1 as boolean) as is_rainy_day,
  cast(w.sunshine_duration / 3600 as double) as sunshine_hours,
  cast(w.wind_speed_10m_max as double) as wind_max_kmh,
  cast(w.weather_code as integer) as weather_code,
  cast(w.weather_description as varchar) as weather_description,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('weather_daily') }} as w
{% if is_incremental() %}
  where w.weather_date >= (
    select max(t.weather_date) - interval '7' day from {{ this }} as t
  )
{% endif %}
