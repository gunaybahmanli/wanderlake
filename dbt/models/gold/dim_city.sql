-- One row per configured city, enriched with its country from dim_country.
select
  c.city_id,
  c.name,
  c.country_code,
  d.name_common as country_name,
  d.region,
  d.subregion,
  d.flag_png_url,
  d.language_names,
  c.timezone,
  c.latitude,
  c.longitude,
  c.currency,
  d.primary_currency_name,
  d.primary_currency_symbol,
  c.rate_source,
  c.inside_airbnb,
  c.geonames_id,
  c.geonames_population,
  c.elevation_m,
  c.elevation_source,
  c.admin1_code,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('cities') }} as c
inner join {{ ref('dim_country') }} as d
  on c.country_code = d.cca2
