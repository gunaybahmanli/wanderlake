-- One row per country (all countries), with a flag for countries that have a configured city.
with configured as (

  select distinct country_code from {{ ref('cities') }}

)

select
  c.cca2,
  c.cca3,
  c.name_common,
  c.name_official,
  c.capital,
  c.region,
  c.subregion,
  c.population,
  c.area_km2,
  c.lat,
  c.lon,
  c.flag_png_url,
  c.independent,
  c.un_member,
  c.primary_currency_code,
  c.primary_currency_name,
  c.primary_currency_symbol,
  c.currency_codes,
  c.language_names,
  cast(k.country_code is not null as boolean) as has_configured_city,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('countries') }} as c
left join configured as k
  on c.cca2 = k.country_code
