-- One row per configured city for the city page: dim_city fields; rates to EUR, USD and AZN as
-- of today (1 when the city's currency is the target itself); today's forecast in the city's own
-- time zone from the latest forecast run; the current FCDO advice; and the 3 best months
-- (highest pleasant_day_share, ties by month order).
with rates_today as (

  select
    from_currency,
    to_currency,
    rate
  from {{ ref('fct_exchange_rate') }}
  where date = current_date

),

forecast_today as (

  select
    f.city_id,
    f.forecast_date,
    f.weather_description,
    f.temperature_2m_max,
    f.temperature_2m_min,
    f.precipitation_probability_max
  from {{ ref('weather_forecast') }} as f
  inner join {{ ref('dim_city') }} as c on f.city_id = c.city_id
  where
    f.is_latest_run
    and f.forecast_date = cast(at_timezone(current_timestamp, c.timezone) as date)

),

advice as (

  select
    country_code,
    has_alert,
    public_updated_at
  from {{ ref('travel_advice') }}
  where is_current

),

ranked_months as (

  select
    city_id,
    month,
    row_number() over (
      partition by city_id
      order by pleasant_day_share desc, month asc
    ) as month_rank
  from {{ ref('agg_city_climate_monthly') }}

),

best_months as (

  select
    city_id,
    array_agg(
      format_datetime(date_add('month', month - 1, date '2000-01-01'), 'MMMM')
      order by month_rank
    ) as best_months
  from ranked_months
  where month_rank <= 3
  group by city_id

)

select
  c.city_id,
  c.name,
  c.country_code,
  c.country_name,
  c.region,
  c.flag_png_url,
  c.timezone,
  c.latitude,
  c.longitude,
  c.currency,
  c.primary_currency_name,
  c.primary_currency_symbol,
  c.language_names,
  c.geonames_population,
  c.elevation_m,
  cast(if(c.currency = 'EUR', 1, eur.rate) as decimal(28, 12)) as rate_to_eur,
  cast(if(c.currency = 'USD', 1, usd.rate) as decimal(28, 12)) as rate_to_usd,
  cast(if(c.currency = 'AZN', 1, azn.rate) as decimal(28, 12)) as rate_to_azn,
  cast(f.forecast_date as date) as forecast_date,
  cast(f.weather_description as varchar) as today_weather_description,
  cast(f.temperature_2m_max as double) as today_temp_max_c,
  cast(f.temperature_2m_min as double) as today_temp_min_c,
  cast(f.precipitation_probability_max as double) as today_precipitation_probability,
  cast(a.has_alert as boolean) as has_travel_alert,
  cast(a.public_updated_at as timestamp(6) with time zone) as advice_updated_at,
  cast(b.best_months as array(varchar)) as best_months,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('dim_city') }} as c
left join rates_today as eur on c.currency = eur.from_currency and eur.to_currency = 'EUR'
left join rates_today as usd on c.currency = usd.from_currency and usd.to_currency = 'USD'
left join rates_today as azn on c.currency = azn.from_currency and azn.to_currency = 'AZN'
left join forecast_today as f on c.city_id = f.city_id
left join advice as a on c.country_code = a.country_code
left join best_months as b on c.city_id = b.city_id
