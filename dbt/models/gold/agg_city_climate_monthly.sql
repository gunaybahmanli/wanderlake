-- Climate per city and calendar month (1-12) over all loaded years of daily history.
-- Pleasant day: maximum temperature between 18 and 28 C (inclusive) and less than 1 mm of
-- precipitation. Shares and averages are computed over days, so a partial month (e.g. the
-- current one) counts with the days it has; avg_rainy_days scales the rainy-day share to the
-- average length of that calendar month. Shares divide by a double: avg() over a decimal literal
-- such as 1.0 keeps its scale in Trino and would round the share to one decimal.
with days as (

  select
    city_id,
    year,
    month,
    temp_max_c,
    temp_min_c,
    sunshine_hours,
    is_rainy_day,
    temp_max_c between 18 and 28 and precipitation_mm < 1 as is_pleasant_day,
    day(last_day_of_month(weather_date)) as days_in_month
  from {{ ref('fct_weather_daily') }}

)

select
  cast(city_id as varchar) as city_id,
  cast(month as integer) as month,  -- noqa: RF04
  cast(count(distinct year) as integer) as years_covered,
  cast(count(*) as integer) as days_covered,
  cast(round(avg(temp_max_c), 1) as double) as avg_temp_max_c,
  cast(round(avg(temp_min_c), 1) as double) as avg_temp_min_c,
  cast(
    round(count_if(is_rainy_day) / cast(count(*) as double) * avg(days_in_month), 1) as double
  ) as avg_rainy_days,
  cast(round(avg(sunshine_hours), 1) as double) as avg_sunshine_hours_per_day,
  cast(round(count_if(is_pleasant_day) / cast(count(*) as double), 3) as double)
    as pleasant_day_share,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from days
group by city_id, month
