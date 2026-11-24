# ADR-0011: City images from Wikimedia Commons via Wikidata

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

The website's city cards and city pages used a neutral placeholder instead of a photo. A real
cover image per city makes the site usable as a product, but it brings obligations that the
other data does not have:

- Every image has its own copyright holder and licence. The site may only show images it is
  allowed to show, and must credit them correctly wherever they appear.
- The choice of image must be reproducible and reviewable, like the rest of the data: no
  hand-copied files without provenance.
- The project runs on free tiers, without paid APIs or extra secrets, and the website is
  served by Vercel from data published to Supabase (ADR-0010).
- There are 10 cities today; the list lives in `config/cities.yaml` and will grow.

## Decision

- **Source:** Wikimedia Commons, chosen through Wikidata. Each city in `config/cities.yaml` has
  a reviewed `wikidata_id` (found through the GeoNames ID, Wikidata property P1566). The image
  is the item's main image (P18): preferred-rank statements first, then normal ones, skipping
  deprecated ones. An optional `cover_image_file` in the config replaces the Wikidata choice for
  a city.
- **Licences:** only CC0, public domain, CC BY and CC BY-SA are allowed. Selection takes the
  first candidate with an allowed licence; if there is none (or an override is not allowed),
  the row is still loaded with `license_family = other`, and the dbt tests on `gold.city_images`
  fail and name the file. Non-commercial (NC) and no-derivatives (ND) variants, GFDL-only and
  Commons' own "Attribution" template are not allowed.
- **Pipeline** (the usual layers): a weekly DAG `wikimedia_city_images` stores the raw Wikidata
  claims and Commons `imageinfo` responses unchanged in `s3://raw/wikimedia/...`, loads
  `bronze.wikimedia_city_images`, and dbt builds `silver.city_images` (latest snapshot, author
  and credit as plain text) and `gold.city_images` (contract and tests: one image per city,
  licence in the allow-list, attribution complete and consistent with the licence). The DAG's
  asset triggers `wanderlake_transform` on its own (OR with the daily inputs), which triggers
  `publish_cloud`: `api.city_images` in Supabase (read-only, like ADR-0010) and Parquet on R2.
- **Etiquette:** a descriptive User-Agent with the repository URL, one request at a time with
  a 1 s pause, `maxlag=5` on Wikidata, retries with backoff: about 20 requests a week.
- **Display:** the browser loads Wikimedia's own thumbnails directly from
  `upload.wikimedia.org`, not through the Next.js image optimiser. `next/image` is kept (space
  reserved from the stored width and height, lazy loading, `srcset`/`sizes`) with a custom
  loader that rewrites the stored 1280 px thumbnail URL to one of Wikimedia's standard widths
  (330, 500, 960, 1280) and accepts only `upload.wikimedia.org/wikipedia/commons/` URLs without a
  query string. Every image always shows a credit: "Photo: author, licence (linked), via
  Wikimedia Commons (linked to the file page)". Without an image, the placeholder is shown.

### Why not the Next.js image optimiser

The first version used the optimiser with `upload.wikimedia.org` in `remotePatterns`. With
real data, 7 of the 10 images failed: Wikimedia answered the optimiser with 429 Too Many
Requests. The optimiser fetches the original on the server once per requested width, several at
once, with the User-Agent `node`, which cannot be configured, so it cannot follow Wikimedia's
User-Agent policy. Vercel's optimiser fetches the same way. Loading Wikimedia's thumbnails in
the browser is how Wikipedia itself serves images, needs no server-side fetch, and uses only
the widths Wikimedia pre-renders (others, such as 640 px, were refused with 400).

## Alternatives considered

- **Unsplash API:** high-quality photos and a simple search, but it needs an API key and an
  application approval for production use, its licence is Unsplash's own (not an open
  licence: no redistribution as a collection), and its guidelines require hotlinking their CDN
  and reporting downloads through the API. Search results are not tied to a stable identity of
  the city, so the choice is not reproducible.
- **Manual uploads** (choose photos by hand and store them in the repository or R2): full
  control over the look, but licences and authors would be tracked by hand, nothing checks
  them, files go stale, and adding a city means manual work. It would also put binary files in
  git or a hand-maintained bucket outside the pipeline.
- **No images:** nothing to license or credit, but the site looks unfinished, and the city
  cards are harder to scan.

## Consequences

- Attribution is part of the data: `gold.city_images` carries author, licence name and link and
  the Commons file page, and every place the website shows an image must render the credit
  (component `PhotoCredit`). Images are not covered by the project's code licence.
- Image choice follows Wikidata: when an editor changes a city's P18, the next weekly run may
  pick another file (with its own licence, checked again). A city left without an allowed
  image fails the build instead of showing something unlicensed; `cover_image_file` fixes it.
- Reviewed IDs, not blind matches: for Baku and Madrid the GeoNames match is a secondary item
  without an image, so the config stores the main items (Q9248, Q2807) with a comment. The
  `wikidata_id` field is reviewed data, not derived at run time.
- The Commons API returns thumbnails on `thumb.wikimedia.org` with tracking parameters; the
  pipeline stores the same path on `upload.wikimedia.org` without a query string, so the
  website allows one image host.
- No WebP/AVIF conversion: photos are served as Wikimedia's JPEG/PNG thumbnails, roughly
  25-50 KB on cards and 175-300 KB for a page header. Visitors' browsers connect to Wikimedia
  to load them (the About page says so).
- **Planned upgrade (phase 2, with galleries):** copy the selected thumbnails into our own
  storage (a public R2 bucket, set up with OpenTofu) in the weekly pipeline and serve them
  through full image optimisation. That removes the dependency on Wikimedia's CDN at view time
  and brings modern formats; it becomes worth the extra infrastructure when there are many
  images per city.
- The weekly image run starts an extra transform and publish run on Mondays. It never blocks
  the daily run: it is OR-ed with the four daily inputs.
