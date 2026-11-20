-- Unified exchange rates with EUR as base (ADR-0008):
--   * EUR = 1 on every ECB date (the feed's own EUR row is replaced by this rule).
--   * ECB (Frankfurter) is the source for every currency it publishes.
--   * CBAR fills only currencies ECB never publishes (e.g. AZN, AED), as cross rates through
--     AZN from CBAR rows of the same rate_date:
--         units_per_eur(X)   = (AZN per 1 EUR) / (AZN per 1 X)
--         units_per_eur(AZN) =  AZN per 1 EUR
-- Divisions use decimal(20, 10) inputs: Trino returns decimal(38, 18) for those, while
-- decimal(38, 18) / decimal(38, 18) would round the result to 6 decimals.
with ecb as (

  select
    rate_date,
    currency_code,
    cast(units_per_eur as decimal(20, 10)) as units_per_eur
  from {{ ref('stg_frankfurter_rates') }}
  -- The ECB feed also lists EUR itself (rate 1); EUR comes from the explicit rule below only.
  where currency_code != 'EUR'

),

ecb_dates as (

  select distinct rate_date from ecb

),

ecb_currencies as (

  select distinct currency_code from ecb
  union distinct
  select 'EUR' as currency_code

),

cbar as (

  select
    rate_date,
    currency_code,
    cast(azn_per_unit as decimal(20, 10)) as azn_per_unit
  from {{ ref('stg_cbar_rates') }}

),

cbar_azn_per_eur as (

  select
    rate_date,
    azn_per_unit as azn_per_eur
  from cbar
  where currency_code = 'EUR'

),

ecb_rows as (

  select
    rate_date,
    currency_code,
    units_per_eur,
    'ecb' as source
  from ecb
  union all
  select
    rate_date,
    'EUR' as currency_code,
    cast(1 as decimal(20, 10)) as units_per_eur,
    'ecb' as source
  from ecb_dates

),

cbar_rows as (

  select
    x.rate_date,
    x.currency_code,
    e.azn_per_eur / x.azn_per_unit as units_per_eur,
    'cbar' as source
  from cbar as x
  inner join cbar_azn_per_eur as e on x.rate_date = e.rate_date
  where x.currency_code not in (select ec.currency_code from ecb_currencies as ec)
  union all
  select
    rate_date,
    'AZN' as currency_code,
    azn_per_eur as units_per_eur,
    'cbar' as source
  from cbar_azn_per_eur

),

unioned as (

  select
    rate_date,
    currency_code,
    cast(units_per_eur as decimal(38, 18)) as units_per_eur,
    source
  from ecb_rows
  union all
  select
    rate_date,
    currency_code,
    cast(units_per_eur as decimal(38, 18)) as units_per_eur,
    source
  from cbar_rows

)

select
  cast(rate_date as date) as rate_date,
  cast(currency_code as varchar) as currency_code,
  cast(round(units_per_eur, 10) as decimal(24, 10)) as units_per_eur,
  cast(source as varchar) as source,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from unioned
