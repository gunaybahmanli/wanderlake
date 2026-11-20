-- Every configured city has at least one day of weather history. Returns cities without any.
select c.city_id
from {{ ref('cities') }} as c
left join {{ ref('weather_daily') }} as w
  on c.city_id = w.city_id
group by c.city_id
having count(w.weather_date) = 0
