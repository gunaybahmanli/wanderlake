-- ECB euro reference rates (Frankfurter), one row per (rate_date, currency_code).
-- Weekend and holiday runs repeat the last business day under their own requested_date;
-- here they collapse to the actual rate_date, keeping the most recently loaded row.
with source as (

  select
    rate_date,
    quote_currency as currency_code,
    cast(rate as decimal(18, 8)) as units_per_eur,
    requested_date,
    _loaded_at
  from {{ source('bronze', 'frankfurter_rates') }}

),

ranked as (

  select
    *,
    row_number() over (
      partition by rate_date, currency_code
      order by _loaded_at desc, requested_date desc
    ) as row_num
  from source

)

select
  rate_date,
  currency_code,
  units_per_eur,
  requested_date,
  _loaded_at
from ranked
where row_num = 1
