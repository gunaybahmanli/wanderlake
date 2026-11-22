-- city_overview has exactly one row per configured city, each with today's forecast.
select
  c.city_id,
  count(o.city_id) as overview_rows,
  count(o.today_weather_description) as rows_with_forecast
from {{ ref('dim_city') }} as c
left join {{ ref('city_overview') }} as o
  on c.city_id = o.city_id
group by c.city_id
having count(o.city_id) != 1 or count(o.today_weather_description) != 1
