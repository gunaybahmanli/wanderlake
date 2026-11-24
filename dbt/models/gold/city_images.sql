-- One cover image per configured city, ready for the website: thumbnail (about 1280 px wide)
-- with its size, and everything the credit line needs (author, licence and its link, the
-- Commons file page). attribution_text is the credit as one line of plain text.
select
  cast(i.city_id as varchar) as city_id,
  cast(i.wikidata_id as varchar) as wikidata_id,
  cast(i.file_name as varchar) as file_name,
  cast(i.file_page_url as varchar) as file_page_url,
  cast(i.thumb_url as varchar) as thumb_url,
  cast(i.thumb_width as integer) as thumb_width,
  cast(i.thumb_height as integer) as thumb_height,
  cast(i.original_width as integer) as original_width,
  cast(i.original_height as integer) as original_height,
  cast(i.artist as varchar) as artist,
  cast(i.credit as varchar) as credit,
  cast(i.license_short_name as varchar) as license_short_name,
  cast(i.license_family as varchar) as license_family,
  cast(i.license_url as varchar) as license_url,
  cast(
    concat('Photo: ', i.artist, ', ', i.license_short_name, ', via Wikimedia Commons') as varchar
  ) as attribution_text,
  cast(i.selection as varchar) as selection,
  cast(i.snapshot_date as date) as snapshot_date,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('silver_city_images') }} as i
inner join {{ ref('dim_city') }} as c
  on i.city_id = c.city_id
