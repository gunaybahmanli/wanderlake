-- FCDO travel advice change history: one row per country and published version
-- (distinct public_updated_at), with the content of the latest snapshot that showed it.
-- valid_from = public_updated_at, valid_to = the next version's public_updated_at.
-- Section texts are plain text from the HTML (see macros/html_to_text.sql for its limits).
with versions as (

  select
    *,
    row_number() over (
      partition by country_code, public_updated_at
      order by snapshot_date desc, _loaded_at desc
    ) as row_num
  from {{ ref('stg_fcdo_advice') }}

),

latest_per_version as (

  select *
  from versions
  where row_num = 1

),

parts as (

  select
    v.country_code,
    v.public_updated_at,
    t.part_number,
    json_extract_scalar(t.part, '$.slug') as part_slug,
    -- Titles can carry trailing (non-breaking) spaces in the source.
    trim(replace(json_extract_scalar(t.part, '$.title'), chr(160), ' ')) as part_title,
    {{ html_to_text("json_extract_scalar(t.part, '$.body')") }} as part_text
  from latest_per_version as v
  cross join
    unnest(cast(json_parse(v.parts_json) as array(json))) with ordinality as t (part, part_number)

),

sections as (

  select
    country_code,
    public_updated_at,
    max(case when part_slug = 'warnings-and-insurance' then part_text end) as summary_text,
    max(case when part_slug = 'safety-and-security' then part_text end) as safety_and_security_text,
    max(case when part_slug = 'entry-requirements' then part_text end) as entry_requirements_text,
    max(case when part_slug = 'health' then part_text end) as health_text,
    array_join(
      array_agg(
        part_title || chr(10) || part_text
        order by part_number
      ),
      chr(10) || chr(10)
    ) as full_text
  from parts
  group by country_code, public_updated_at

)

select
  cast(v.country_code as varchar) as country_code,
  cast(v.slug as varchar) as slug,
  cast(v.title as varchar) as title,
  cast(v.public_updated_at as timestamp(6) with time zone) as public_updated_at,
  cast(v.change_description as varchar) as change_description,
  cast(json_parse(v.alert_status_json) as array(varchar)) as alert_statuses,
  cast(cardinality(cast(json_parse(v.alert_status_json) as array(varchar))) > 0 as boolean)
    as has_alert,
  cast(s.summary_text as varchar) as summary_text,
  cast(s.safety_and_security_text as varchar) as safety_and_security_text,
  cast(s.entry_requirements_text as varchar) as entry_requirements_text,
  cast(s.health_text as varchar) as health_text,
  cast(s.full_text as varchar) as full_text,
  cast(v.public_updated_at as timestamp(6) with time zone) as valid_from,
  cast(
    lead(v.public_updated_at) over (
      partition by v.country_code order by v.public_updated_at
    ) as timestamp(6) with time zone
  ) as valid_to,
  cast(
    lead(v.public_updated_at) over (
      partition by v.country_code order by v.public_updated_at
    ) is null as boolean
  ) as is_current,
  cast(v.snapshot_date as date) as last_seen_snapshot_date,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from latest_per_version as v
inner join sections as s
  on v.country_code = s.country_code and v.public_updated_at = s.public_updated_at
