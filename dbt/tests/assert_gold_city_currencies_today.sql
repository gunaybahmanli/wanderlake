-- Every configured city currency appears in the conversion matrix for today.
select c.currency
from (select distinct currency from {{ ref('dim_city') }}) as c
left join {{ ref('fct_exchange_rate') }} as r
  on c.currency = r.from_currency and r.date = current_date
group by c.currency
having count(r.from_currency) = 0
