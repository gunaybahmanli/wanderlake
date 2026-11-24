-- The attribution of every published image is complete and consistent with its licence:
--   * an author, a licence name and the Commons file page are present (not just non-null)
--   * CC BY and CC BY-SA images link to their licence text
--   * the licence name matches its family (a second check on the classification in
--     ingestion.sources.wikimedia.license_family): no NC or ND variant gets through
select
  city_id,
  file_name,
  license_short_name,
  license_family
from {{ ref('city_images') }}
where
  coalesce(trim(artist), '') = ''
  or coalesce(trim(license_short_name), '') = ''
  or coalesce(file_page_url, '') not like 'https://commons.wikimedia.org/%'
  or (license_family in ('cc-by', 'cc-by-sa') and coalesce(license_url, '') = '')
  or regexp_like(license_short_name, '(?i)\b(nc|nd)\b')
  or (license_family = 'cc-by-sa' and not regexp_like(license_short_name, '(?i)^cc by-sa '))
  or (license_family = 'cc-by' and not regexp_like(license_short_name, '(?i)^cc by \d'))
  or (license_family = 'cc0' and not regexp_like(license_short_name, '(?i)^cc0'))
