-- USD per EUR stays between 0.8 and 1.5 (a sanity range). Returns offending rows.
select
  rate_date,
  units_per_eur
from {{ ref('exchange_rates') }}
where
  currency_code = 'USD'
  and (units_per_eur < 0.8 or units_per_eur > 1.5)
