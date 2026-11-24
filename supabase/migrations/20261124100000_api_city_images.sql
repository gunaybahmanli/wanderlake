-- api.city_images: one cover image per city from Wikimedia Commons (gold.city_images,
-- ADR-0011), with everything the website's credit line needs. Read-only for the anon role,
-- like every table in schema api (ADR-0010): row-level security with a SELECT-only policy,
-- SELECT grant only. Idempotent: safe to run again.

create table if not exists api.city_images (
  city_id text primary key,
  wikidata_id text,
  file_name text not null,
  file_page_url text not null,
  thumb_url text not null,
  thumb_width integer not null,
  thumb_height integer not null,
  original_width integer,
  original_height integer,
  artist text not null,
  credit text,
  license_short_name text not null,
  license_family text not null,
  license_url text,
  attribution_text text,
  selection text,
  snapshot_date date,
  _built_at timestamptz
);

alter table api.city_images enable row level security;
revoke all on table api.city_images from public, anon, authenticated;
grant select on table api.city_images to anon;
drop policy if exists anon_read on api.city_images;
create policy anon_read on api.city_images as permissive for select to anon using (true);
