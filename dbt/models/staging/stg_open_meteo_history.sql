-- Open-Meteo history, one row per (city_id, weather_date), keeping the most recent load.
with ranked as (

  select
    *,
    row_number() over (
      partition by city_id, weather_date
      order by _loaded_at desc
    ) as row_num
  from {{ source('bronze', 'open_meteo_history') }}

)

select
  city_id,
  weather_date,
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
  _loaded_at
from ranked
where row_num = 1
