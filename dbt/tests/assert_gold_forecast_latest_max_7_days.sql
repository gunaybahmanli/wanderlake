-- weather_forecast_latest: every configured city has between 1 and 7 forecast days.
select
  c.city_id,
  count(f.forecast_date) as forecast_days
from {{ ref('dim_city') }} as c
left join {{ ref('weather_forecast_latest') }} as f
  on c.city_id = f.city_id
group by c.city_id
having count(f.forecast_date) not between 1 and 7
