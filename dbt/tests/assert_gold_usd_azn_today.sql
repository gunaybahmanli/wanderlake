-- 100 USD -> AZN on today's date is between 169 and 171 (the manat is pegged at about 1.70).
-- Returns a row if it is outside the band or missing.
with today as (
  select max(100 * rate) as azn_for_100_usd
  from {{ ref('fct_exchange_rate') }}
  where date = current_date and from_currency = 'USD' and to_currency = 'AZN'
)

select azn_for_100_usd
from today
where azn_for_100_usd is null or azn_for_100_usd not between 169 and 171
