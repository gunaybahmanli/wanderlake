-- 7-day forecast per configured city from that city's latest forecast run (normally the same
-- run for every city; per city, so a city missing from the newest run keeps its last forecast).
-- Days from run_date to run_date + 6 only.
with forecasts as (

  select
    *,
    max(run_date) over (partition by city_id) as latest_run_date
  from {{ ref('weather_forecast') }}

)

select
  cast(city_id as varchar) as city_id,
  cast(forecast_date as date) as forecast_date,
  cast(run_date as date) as run_date,
  cast(weather_code as integer) as weather_code,
  cast(weather_description as varchar) as weather_description,
  cast(temperature_2m_max as double) as temp_max_c,
  cast(temperature_2m_min as double) as temp_min_c,
  cast(temperature_2m_mean as double) as temp_mean_c,
  cast(precipitation_sum as double) as precipitation_mm,
  cast(precipitation_probability_max as double) as precipitation_probability,
  cast(snowfall_sum as double) as snowfall_cm,
  cast(wind_speed_10m_max as double) as wind_max_kmh,
  cast(round(sunshine_duration / 3600.0, 1) as double) as sunshine_hours,
  cast(uv_index_max as double) as uv_index_max,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from forecasts
where
  run_date = latest_run_date
  and forecast_date between run_date and date_add('day', 6, run_date)
