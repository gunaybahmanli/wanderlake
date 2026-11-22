-- Every configured city has exactly 12 months in agg_city_climate_monthly.
select
  c.city_id,
  count(a.month) as months
from {{ ref('dim_city') }} as c
left join {{ ref('agg_city_climate_monthly') }} as a
  on c.city_id = a.city_id
group by c.city_id
having count(a.month) != 12
