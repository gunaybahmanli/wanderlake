-- AZN <-> USD in the conversion matrix is CBAR's direct, official rate (ADR-0008): the manat is
-- pegged at 1.7000 AZN per USD, so every USD -> AZN row is between 1.6999 and 1.7001 and every
-- AZN -> USD row is its inverse, and both sides come from CBAR. Returns the offending rows,
-- including any USD/AZN row that is still a cross rate.
select
  date,
  from_currency,
  to_currency,
  rate,
  source_from,
  source_to
from {{ ref('fct_exchange_rate') }}
where
  (from_currency = 'USD' and to_currency = 'AZN' or from_currency = 'AZN' and to_currency = 'USD')
  and (
    source_from != 'cbar'
    or source_to != 'cbar'
    or source_from is null
    or source_to is null
    or (from_currency = 'USD' and rate not between 1.6999 and 1.7001)
    or (from_currency = 'AZN' and rate not between 1 / 1.7001 and 1 / 1.6999)
  )
