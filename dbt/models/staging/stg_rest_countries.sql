-- REST Countries: the latest weekly snapshot, one row per country (ISO 3166 code).
select
  s.snapshot_date,
  s.cca2,
  s.cca3,
  s.name_common,
  s.name_official,
  s.capital,
  s.region,
  s.subregion,
  s.population,
  s.area_km2,
  s.lat,
  s.lon,
  s.flag_png_url,
  s.independent,
  s.un_member,
  s.currencies_json,
  s.languages_json,
  s._loaded_at
from {{ source('bronze', 'rest_countries') }} as s
where s.snapshot_date = (
  select max(latest.snapshot_date) from {{ source('bronze', 'rest_countries') }} as latest
)
