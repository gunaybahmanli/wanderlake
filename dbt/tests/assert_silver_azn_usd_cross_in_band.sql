-- Sanity band for the cross rates in silver.exchange_rates. The manat is pegged to the dollar at
-- 1.70, but here AZN per USD is a cross rate of two sources: units_per_eur(AZN) from CBAR (fixed
-- in the Baku morning) divided by units_per_eur(USD) from the ECB (fixed at 14:15 CET). On days
-- when EUR/USD moves a lot between the two fixings they differ by up to about 0.75% (seen on
-- 2025-10-30, 2026-03-02, 2026-03-03), so the band is 1.68 to 1.72 (about 1.2%): wide enough for
-- that, narrow enough to catch a wrong currency, an inverted rate or a nominal error in the
-- values that cross rates (for example AZN <-> THB) are built from. The official AZN <-> USD rate
-- that the site shows is direct from CBAR and checked strictly in
-- assert_gold_azn_usd_official.sql (ADR-0008).
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
where azn.units_per_eur / usd.units_per_eur not between 1.68 and 1.72
