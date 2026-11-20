-- Every currency of a configured city (bronze.config_cities, copied from config/cities.yaml)
-- must have a rate on the latest rate_date that has both ECB and CBAR rows.
-- Returns the missing currencies (and fails if there is no such date at all).
with latest as (
  select max(rate_date) as rate_date
  from (
    select rate_date
    from {{ ref('exchange_rates') }}
    group by rate_date
    having count(distinct source) = 2
  ) as both_sources
),

city_currencies as (
  select distinct currency from {{ source('bronze', 'config_cities') }}
)

select
  c.currency,
  l.rate_date
from city_currencies as c
cross join latest as l
left join {{ ref('exchange_rates') }} as r
  on c.currency = r.currency_code and l.rate_date = r.rate_date
where l.rate_date is null or r.currency_code is null
