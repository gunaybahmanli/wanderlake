-- Daily conversion matrix for the currencies of interest: every configured city currency plus
-- EUR, USD and AZN. One row per calendar date and ordered currency pair (no identical pairs).
-- As-of rule: each calendar date uses, per currency, the latest units_per_eur with
-- rate_date <= date, so weekends and holidays have values; the rate dates used are kept.
-- rate = units of to_currency per 1 unit of from_currency.
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

)

select
  -- "date" is the column name the website expects; it is a keyword, hence noqa.
  cast(f.date_day as date) as date,  -- noqa: RF04
  cast(f.currency_code as varchar) as from_currency,
  cast(t.currency_code as varchar) as to_currency,
  cast(
    cast(t.units_per_eur as decimal(20, 10)) / cast(f.units_per_eur as decimal(20, 10))
    as decimal(28, 12)
  ) as rate,
  cast(f.rate_date as date) as from_rate_date,
  cast(t.rate_date as date) as to_rate_date,
  cast(f.source as varchar) as source_from,
  cast(t.source as varchar) as source_to,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from daily as f
inner join daily as t
  on f.date_day = t.date_day and f.currency_code != t.currency_code
