-- One row per country (ISO 3166 code) from the latest REST Countries snapshot.
-- The JSON lists from bronze are flattened: currencies keep REST Countries' order, and the
-- first one is the "primary" currency (null for the few countries without a currency).
with source as (

  select
    *,
    cast(
      json_parse(currencies_json) as array(row(code varchar, name varchar, symbol varchar))
    ) as currencies,
    cast(json_parse(languages_json) as array(json)) as languages
  from {{ ref('stg_rest_countries') }}

)

select
  cast(cca2 as varchar) as cca2,
  cast(cca3 as varchar) as cca3,
  cast(name_common as varchar) as name_common,
  cast(name_official as varchar) as name_official,
  cast(capital as varchar) as capital,
  cast(region as varchar) as region,
  cast(subregion as varchar) as subregion,
  cast(population as bigint) as population,
  cast(area_km2 as double) as area_km2,
  cast(lat as double) as lat,
  cast(lon as double) as lon,
  cast(flag_png_url as varchar) as flag_png_url,
  cast(independent as boolean) as independent,
  cast(un_member as boolean) as un_member,
  cast(element_at(currencies, 1).code as varchar) as primary_currency_code,
  cast(element_at(currencies, 1).name as varchar) as primary_currency_name,
  cast(element_at(currencies, 1).symbol as varchar) as primary_currency_symbol,
  cast(transform(currencies, c -> c.code) as array(varchar)) as currency_codes,
  cast(
    transform(languages, l -> json_extract_scalar(l, '$.name')) as array(varchar)
  ) as language_names,
  cast(snapshot_date as date) as snapshot_date,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from source
