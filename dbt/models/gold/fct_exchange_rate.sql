-- Daily conversion matrix for the currencies of interest: every configured city currency plus
-- EUR, USD and AZN. One row per calendar date and ordered currency pair (no identical pairs).
-- As-of rule: each calendar date uses, per currency, the latest units_per_eur with
-- rate_date <= date, so weekends and holidays have values; the rate dates used are kept.
-- rate = units of to_currency per 1 unit of from_currency.
-- Pairs with AZN use CBAR's direct rate (ADR-0008): when the CBAR bulletin that gives AZN its
-- value on a date also lists the other currency X, X -> AZN = AZN per 1 X and AZN -> X is its
-- inverse, both sides dated by that bulletin and sourced "cbar". So AZN <-> USD is always the
-- official rate (1.7000). Other pairs, and AZN pairs whose currency CBAR does not publish (THB),
-- are cross rates through the EUR base.
-- Divisions use decimal(20, 10) inputs (Trino keeps 18 decimals then; see ADR-0008).
with currencies as (

  select distinct currency as currency_code from {{ ref('dim_city') }}
  union distinct
  select v.currency_code from (values 'EUR', 'USD', 'AZN') as v (currency_code)

),

rates as (

  select
    r.rate_date,
    r.currency_code,
    r.units_per_eur,
    r.source
  from {{ ref('exchange_rates') }} as r
  inner join currencies as c on r.currency_code = c.currency_code

),

calendar as (

  -- Trino-native date spine: first available rate date up to today (UTC).
  select t.date_day
  from (select min(rate_date) as first_date from rates) as b
  cross join unnest(sequence(b.first_date, current_date, interval '1' day)) as t (date_day)

),

as_of as (

  select
    cal.date_day,
    c.currency_code,
    max(r.rate_date) as rate_date
  from calendar as cal
  cross join currencies as c
  inner join rates as r
    on c.currency_code = r.currency_code and cal.date_day >= r.rate_date
  group by cal.date_day, c.currency_code

),

daily as (

  select
    a.date_day,
    a.currency_code,
    a.rate_date,
    r.units_per_eur,
    r.source
  from as_of as a
  inner join rates as r
    on a.currency_code = r.currency_code and a.rate_date = r.rate_date

),

cbar_direct as (

  -- AZN per 1 unit of X from the same CBAR bulletin as AZN's own value on each date.
  select
    azn.date_day,
    x.currency_code,
    azn.rate_date,
    cast(x.azn_per_unit as decimal(20, 10)) as azn_per_unit
  from daily as azn
  inner join {{ ref('stg_cbar_rates') }} as x
    on azn.rate_date = x.rate_date
  inner join currencies as c on x.currency_code = c.currency_code
  where azn.currency_code = 'AZN' and x.currency_code != 'AZN'

),

cross_rates as (

  select
    f.date_day,
    f.currency_code as from_currency,
    t.currency_code as to_currency,
    f.rate_date as from_rate_date,
    t.rate_date as to_rate_date,
    f.source as source_from,
    t.source as source_to,
    cast(t.units_per_eur as decimal(20, 10)) / cast(f.units_per_eur as decimal(20, 10)) as rate
  from daily as f
  inner join daily as t
    on f.date_day = t.date_day and f.currency_code != t.currency_code

),

pairs as (

  select
    r.date_day,
    r.from_currency,
    r.to_currency,
    case
      when r.to_currency = 'AZN' and d.azn_per_unit is not null then d.azn_per_unit
      when r.from_currency = 'AZN' and d.azn_per_unit is not null
        then cast(1 as decimal(20, 10)) / d.azn_per_unit
      else r.rate
    end as rate,
    coalesce(d.rate_date, r.from_rate_date) as from_rate_date,
    coalesce(d.rate_date, r.to_rate_date) as to_rate_date,
    case when d.azn_per_unit is not null then 'cbar' else r.source_from end as source_from,
    case when d.azn_per_unit is not null then 'cbar' else r.source_to end as source_to
  from cross_rates as r
  left join cbar_direct as d
    on
      r.date_day = d.date_day
      and (
        (r.to_currency = 'AZN' and r.from_currency = d.currency_code)
        or (r.from_currency = 'AZN' and r.to_currency = d.currency_code)
      )

)

select
  -- "date" is the column name the website expects; it is a keyword, hence noqa.
  cast(date_day as date) as date,  -- noqa: RF04
  cast(from_currency as varchar) as from_currency,
  cast(to_currency as varchar) as to_currency,
  cast(rate as decimal(28, 12)) as rate,
  cast(from_rate_date as date) as from_rate_date,
  cast(to_rate_date as date) as to_rate_date,
  cast(source_from as varchar) as source_from,
  cast(source_to as varchar) as source_to,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from pairs
