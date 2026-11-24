-- Table silver.city_images (model name silver_city_images: dbt model names are unique across
-- the project, and gold.city_images keeps the plain name).
{{ config(alias='city_images') }}

-- Cover image per configured city from the latest snapshot that has one for that city, with
-- the artist and credit as plain text (macros/html_to_text.sql). Commons artist fields are
-- usually a link to the author's user page: the link target is dropped, the name is kept.
with ranked as (

  select
    *,
    row_number() over (
      partition by city_id
      order by snapshot_date desc, _loaded_at desc
    ) as row_num
  from {{ ref('stg_wikimedia_city_images') }}

)

select
  cast(city_id as varchar) as city_id,
  cast(wikidata_id as varchar) as wikidata_id,
  cast(file_name as varchar) as file_name,
  cast(selection as varchar) as selection,
  cast(file_page_url as varchar) as file_page_url,
  cast(original_url as varchar) as original_url,
  cast(original_width as integer) as original_width,
  cast(original_height as integer) as original_height,
  cast(thumb_url as varchar) as thumb_url,
  cast(thumb_width as integer) as thumb_width,
  cast(thumb_height as integer) as thumb_height,
  cast(mime as varchar) as mime,
  cast(license_short_name as varchar) as license_short_name,
  cast(license_code as varchar) as license_code,
  cast(license_url as varchar) as license_url,
  cast(license_family as varchar) as license_family,
  cast(nullif({{ html_to_text('artist_html') }}, '') as varchar) as artist,
  cast(nullif({{ html_to_text('credit_html') }}, '') as varchar) as credit,
  cast(attribution_required as boolean) as attribution_required,
  cast(snapshot_date as date) as snapshot_date,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from ranked
where row_num = 1
