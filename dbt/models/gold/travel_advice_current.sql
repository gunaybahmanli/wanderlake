-- Current UK FCDO travel advice per country: the latest published version from
-- silver.travel_advice (is_current), with the sections the website shows.
select
  cast(country_code as varchar) as country_code,
  cast(slug as varchar) as slug,
  cast(title as varchar) as title,
  cast(has_alert as boolean) as has_alert,
  cast(alert_statuses as array(varchar)) as alert_statuses,
  cast(public_updated_at as timestamp(6) with time zone) as public_updated_at,
  cast(change_description as varchar) as change_description,
  cast(summary_text as varchar) as summary_text,
  cast(safety_and_security_text as varchar) as safety_text,
  cast(entry_requirements_text as varchar) as entry_requirements_text,
  cast(health_text as varchar) as health_text,
  cast(last_seen_snapshot_date as date) as last_seen_snapshot_date,
  cast(current_timestamp as timestamp(6) with time zone) as _built_at
from {{ ref('travel_advice') }}
where is_current
