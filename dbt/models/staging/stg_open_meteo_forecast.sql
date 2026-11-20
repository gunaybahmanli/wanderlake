-- Open-Meteo forecasts, one row per (run_date, city_id, forecast_date), keeping the latest load.
with ranked as (

  select
    *,
    row_number() over (
      partition by run_date, city_id, forecast_date
      order by _loaded_at desc
    ) as row_num
  from {{ source('bronze', 'open_meteo_forecast') }}

)

select
  run_date,
  city_id,
  forecast_date,
  weather_code,
  temperature_2m_max,
  temperature_2m_min,
  temperature_2m_mean,
  precipitation_sum,
  rain_sum,
  snowfall_sum,
  precipitation_hours,
  wind_speed_10m_max,
  sunshine_duration,
  uv_index_max,
  precipitation_probability_max,
  _loaded_at
from ranked
where row_num = 1
