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

  # Ignored build step (runs in web/): exit 0 = skip, 1 = build. Compares against the last
  # deployed commit, so a branch builds if any commit since then touched web/. If that commit
  # is unknown or missing from the clone, git errors (non-zero) and Vercel builds anyway.
  ignore_command = "if [ -z \"$VERCEL_GIT_PREVIOUS_SHA\" ]; then exit 1; fi; git diff --quiet \"$VERCEL_GIT_PREVIOUS_SHA\" HEAD -- ."

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
