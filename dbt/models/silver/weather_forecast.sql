-- Daily forecasts per configured city (Open-Meteo), one row per
-- (run_date, city_id, forecast_date). is_latest_run marks the most recent run.
select
  cast(f.run_date as date) as run_date,
  cast(f.city_id as varchar) as city_id,
  cast(f.forecast_date as date) as forecast_date,
  cast(f.weather_code as integer) as weather_code,
  cast(w.weather_description as varchar) as weather_description,
  cast(f.temperature_2m_max as double) as temperature_2m_max,
  cast(f.temperature_2m_min as double) as temperature_2m_min,
  cast(f.temperature_2m_mean as double) as temperature_2m_mean,
  cast(f.precipitation_sum as double) as precipitation_sum,
  cast(f.rain_sum as double) as rain_sum,
  cast(f.snowfall_sum as double) as snowfall_sum,
  cast(f.precipitation_hours as double) as precipitation_hours,
  cast(f.wind_speed_10m_max as double) as wind_speed_10m_max,
  cast(f.sunshine_duration as double) as sunshine_duration,
  cast(f.uv_index_max as double) as uv_index_max,
  cast(f.precipitation_probability_max as double) as precipitation_probability_max,
  cast(f.run_date = max(f.run_date) over () as boolean) as is_latest_run,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('stg_open_meteo_forecast') }} as f
left join {{ ref('wmo_weather_codes') }} as w
  on f.weather_code = w.weather_code
