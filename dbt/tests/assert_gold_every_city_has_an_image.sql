-- Every configured city has exactly one cover image (a city whose Wikidata item has no image
-- with an allowed licence shows up here; fix it with cover_image_file in config/cities.yaml).
select
  c.city_id,
  count(i.city_id) as images
from {{ ref('dim_city') }} as c
left join {{ ref('city_images') }} as i
  on c.city_id = i.city_id
group by c.city_id
having count(i.city_id) != 1
