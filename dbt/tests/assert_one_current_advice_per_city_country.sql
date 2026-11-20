-- The country of every configured city has exactly one current travel_advice row.
select
  c.country_code,
  count(a.country_code) as current_rows
from (select distinct country_code from {{ ref('cities') }}) as c
left join {{ ref('travel_advice') }} as a
  on c.country_code = a.country_code and a.is_current
group by c.country_code
having count(a.country_code) != 1
