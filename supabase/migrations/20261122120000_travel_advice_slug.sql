-- api.travel_advice_current.slug: the GOV.UK page slug, so the website can link to the official
-- advice page (https://www.gov.uk/foreign-travel-advice/<slug>). Added after the first publish.
-- Idempotent: safe to run again. RLS, the policy and the grants are per table, so they cover
-- the new column.

alter table api.travel_advice_current add column if not exists slug text;
