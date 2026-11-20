-- CBAR official rates (AZN per foreign unit), one row per (rate_date, currency_code).
-- azn_per_unit = rate_azn / nominal (e.g. JPY is quoted per 100 yen).
-- Weekend and holiday repeats collapse to the actual rate_date, keeping the latest load.
with source as (

  select
    rate_date,
    currency_code,
    currency_name,
    nominal,
    cast(rate_azn as decimal(18, 6)) as rate_azn,
    requested_date,
    _loaded_at,
    cast(rate_azn as decimal(18, 6)) / nominal as azn_per_unit
  from {{ source('bronze', 'cbar_rates') }}

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
  currency_name,
  nominal,
  rate_azn,
  azn_per_unit,
  requested_date,
  _loaded_at
from ranked
where row_num = 1
