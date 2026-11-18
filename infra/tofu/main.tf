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
