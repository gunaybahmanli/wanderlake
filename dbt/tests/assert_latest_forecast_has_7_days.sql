-- In the latest forecast run, every configured city has exactly 7 forecast days.
select
  c.city_id,
  count(f.forecast_date) as forecast_days
from {{ ref('cities') }} as c
left join {{ ref('weather_forecast') }} as f
  on c.city_id = f.city_id and f.is_latest_run
group by c.city_id
having count(f.forecast_date) != 7
