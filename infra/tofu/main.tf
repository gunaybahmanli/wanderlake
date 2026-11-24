# Cloudflare R2: S3-compatible object storage for the always-on cloud lakehouse
# (the cloud counterpart of the local SeaweedFS "lakehouse" bucket). Free tier: 10 GB storage,
# no egress fees. The location is a best-effort hint, honored only when the bucket is created.
resource "cloudflare_r2_bucket" "lake" {
  account_id    = var.cloudflare_account_id
  name          = var.r2_bucket_name
  location      = var.r2_location
  storage_class = "Standard"

  lifecycle {
    prevent_destroy = true # protects lake data from an accidental destroy or rename
  }
}

# Raw landing zone of the production pipeline (ADR-0012): source responses stored unchanged, the
# cloud counterpart of the local SeaweedFS "raw" bucket. The prod Iceberg warehouse lives in the
# lake bucket above (prefix warehouse/), next to the published Parquet (prefix gold/).
resource "cloudflare_r2_bucket" "raw" {
  account_id    = var.cloudflare_account_id
  name          = var.r2_raw_bucket_name
  location      = var.r2_location
  storage_class = "Standard"

  lifecycle {
    prevent_destroy = true # raw responses are the replayable source of truth
  }
}

# Supabase: managed Postgres (with pgvector available) for the cloud app database and API.
# Free tier: no instance_size is set, so the project gets the free compute size.
resource "supabase_project" "wanderlake" {
  organization_id   = var.supabase_org_id
  name              = var.supabase_project_name
  region            = var.supabase_region
  database_password = var.supabase_db_password

  lifecycle {
    prevent_destroy = true # protects the database from an accidental destroy or rename
  }
}

# Vercel: the Next.js website in web/. Pushes to main deploy to production; every other
# branch or PR gets a preview deployment (behind Vercel login: Standard Protection).
resource "vercel_project" "wanderlake" {
  name           = var.vercel_project_name
  framework      = "nextjs"
  root_directory = "web"
  node_version   = "26.x"

  git_repository = {
    type              = "github"
    repo              = "gunaybahmanli/wanderlake"
    production_branch = "main"
  }

  # Expose VERCEL_GIT_COMMIT_SHA and friends to the build (the footer shows the commit).
  automatically_expose_system_environment_variables = true

  # Ignored build step (runs in web/): exit 0 = skip, 1 = build. Vercel fails the deployment
  # on any other exit code. Compares against the last deployed commit, so a branch builds if any
  # commit since then touched web/. Vercel clones only recent history, so that commit can be
  # missing (git would fail with exit 128, "bad object"): then build instead of failing.
  ignore_command = "if [ -z \"$VERCEL_GIT_PREVIOUS_SHA\" ] || ! git cat-file -e \"$VERCEL_GIT_PREVIOUS_SHA^{commit}\" 2>/dev/null; then exit 1; fi; git diff --quiet \"$VERCEL_GIT_PREVIOUS_SHA\" HEAD -- ."

  # Keep Vercel's default: previews require a Vercel login, production is public.
  vercel_authentication = {
    deployment_type = "standard_protection_new"
  }
}

# Production domain for the project (free *.vercel.app subdomain).
resource "vercel_project_domain" "production" {
  project_id = vercel_project.wanderlake.id
  domain     = var.web_domain
}

# Supabase REST API (PostgREST): expose schema "api", the read-only gold tables the website
# reads with the anon key (ADR-0010). Tables, RLS and grants live in supabase/migrations and are
# applied by the publish job. Only the keys listed here are managed; the rest keep their values.
# max_rows caps one response; the website pages through larger tables.
resource "supabase_settings" "wanderlake" {
  project_ref = supabase_project.wanderlake.id

  api = jsonencode({
    db_schema            = "public,graphql_public,api"
    db_extra_search_path = "public, extensions"
    max_rows             = 1000
  })
}

# Website -> Supabase REST API (ADR-0010). Server-side variables (not NEXT_PUBLIC_*): the pages
# read Supabase on the server, so the key is never sent to browsers. Production and preview
# builds; local development uses web/.env.local (make web-env).
resource "vercel_project_environment_variable" "supabase_url" {
  project_id = vercel_project.wanderlake.id
  key        = "SUPABASE_URL"
  value      = "https://${supabase_project.wanderlake.id}.supabase.co"
  target     = ["production", "preview"]
  sensitive  = false # the project URL is an identifier, not a secret
  comment    = "Supabase project URL for the server-side data layer (web/lib/data)."
}

resource "vercel_project_environment_variable" "supabase_anon_key" {
  project_id = vercel_project.wanderlake.id
  key        = "SUPABASE_ANON_KEY"
  # Write-only: sent to Vercel but never stored in OpenTofu state. Changing the key alone does
  # not trigger an update; bump supabase_anon_key_version after a rotation.
  value_wo         = var.supabase_anon_key
  value_wo_version = var.supabase_anon_key_version
  target           = ["production", "preview"]
  sensitive        = true # Vercel hides the value after creation
  comment          = "Supabase anon key (read-only by RLS) for the server-side data layer."
}
