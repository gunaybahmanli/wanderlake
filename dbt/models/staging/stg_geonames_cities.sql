-- GeoNames cities15000: the latest monthly snapshot, one row per place.
select
  s.snapshot_date,
  s.geonameid,
  s.name,
  s.country_code,
  s.admin1_code,
  s.population,
  s.elevation,
  s.dem,
  s.timezone,
  s._loaded_at
from {{ source('bronze', 'geonames_cities') }} as s
where s.snapshot_date = (
  select max(latest.snapshot_date) from {{ source('bronze', 'geonames_cities') }} as latest
)
