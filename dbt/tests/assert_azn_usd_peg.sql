-- The manat is pegged to the dollar at about 1.70: AZN per USD must stay between 1.69 and 1.71
-- on every date where both currencies exist. AZN per USD = units_per_eur(AZN) / units_per_eur(USD).
with azn as (
  select
    rate_date,
    units_per_eur
  from {{ ref('exchange_rates') }}
  where currency_code = 'AZN'
),

usd as (
  select
    rate_date,
    units_per_eur
  from {{ ref('exchange_rates') }}
  where currency_code = 'USD'
)

select
  azn.rate_date,
  azn.units_per_eur / usd.units_per_eur as azn_per_usd
from azn
inner join usd on azn.rate_date = usd.rate_date
where azn.units_per_eur / usd.units_per_eur not between 1.69 and 1.71
