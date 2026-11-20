-- One row per configured city: config/cities.yaml (via bronze.config_cities; our coordinates)
-- joined to the latest GeoNames snapshot on geonames_id.
-- elevation_m is GeoNames' elevation, falling back to its terrain model (dem) when empty.
select
  cast(c.city_id as varchar) as city_id,
  cast(c.name as varchar) as name,
  cast(c.country_code as varchar) as country_code,
  cast(c.timezone as varchar) as timezone,
  cast(c.latitude as double) as latitude,
  cast(c.longitude as double) as longitude,
  cast(c.currency as varchar) as currency,
  cast(c.rate_source as varchar) as rate_source,
  cast(c.inside_airbnb as boolean) as inside_airbnb,
  cast(c.geonames_id as bigint) as geonames_id,
  cast(g.population as bigint) as geonames_population,
  cast(coalesce(g.elevation, g.dem) as integer) as elevation_m,
  cast(
    case
      when g.elevation is not null then 'elevation'
      when g.dem is not null then 'dem'
    end as varchar
  ) as elevation_source,
  cast(g.admin1_code as varchar) as admin1_code,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ source('bronze', 'config_cities') }} as c
left join {{ ref('stg_geonames_cities') }} as g
  on c.geonames_id = g.geonameid
