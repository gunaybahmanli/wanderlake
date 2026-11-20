-- FCDO travel advice: every daily snapshot, one row per (snapshot_date, country_code).
select
  snapshot_date,
  country_code,
  slug,
  title,
  public_updated_at,
  change_description,
  alert_status_json,
  parts_json,
  _loaded_at
from {{ source('bronze', 'fcdo_advice') }}
