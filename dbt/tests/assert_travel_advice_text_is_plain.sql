-- The cleaned texts contain no HTML tags and no undecoded named entities. Returns offenders.
select
  country_code,
  public_updated_at,
  regexp_extract(full_text, '<[a-zA-Z/][^>]*>') as leftover_tag,
  regexp_extract(full_text, '&[a-zA-Z]+;') as leftover_entity
from {{ ref('travel_advice') }}
where
  regexp_like(full_text, '<[a-zA-Z/][^>]*>')
  or regexp_like(full_text, '&[a-zA-Z]+;')
  or regexp_like(full_text, ' \n|\n ')
