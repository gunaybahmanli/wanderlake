-- Daily observed weather per configured city (Open-Meteo archive), one row per
-- (city_id, weather_date), with the WMO weather code's description.
select
  cast(h.city_id as varchar) as city_id,
  cast(h.weather_date as date) as weather_date,
  cast(h.weather_code as integer) as weather_code,
  cast(w.weather_description as varchar) as weather_description,
  cast(h.temperature_2m_max as double) as temperature_2m_max,
  cast(h.temperature_2m_min as double) as temperature_2m_min,
  cast(h.temperature_2m_mean as double) as temperature_2m_mean,
  cast(h.precipitation_sum as double) as precipitation_sum,
  cast(h.rain_sum as double) as rain_sum,
  cast(h.snowfall_sum as double) as snowfall_sum,
  cast(h.precipitation_hours as double) as precipitation_hours,
  cast(h.wind_speed_10m_max as double) as wind_speed_10m_max,
  cast(h.sunshine_duration as double) as sunshine_duration,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('stg_open_meteo_history') }} as h
left join {{ ref('wmo_weather_codes') }} as w
  on h.weather_code = w.weather_code
