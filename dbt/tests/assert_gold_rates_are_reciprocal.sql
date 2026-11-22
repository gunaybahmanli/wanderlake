-- For every date and pair, rate(A->B) * rate(B->A) is between 0.999 and 1.001.
select
  ab.date,
  ab.from_currency,
  ab.to_currency,
  ab.rate * ba.rate as round_trip
from {{ ref('fct_exchange_rate') }} as ab
inner join {{ ref('fct_exchange_rate') }} as ba
  on
    ab.date = ba.date
    and ab.from_currency = ba.to_currency
    and ab.to_currency = ba.from_currency
where ab.rate * ba.rate not between 0.999 and 1.001
