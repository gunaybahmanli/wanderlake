# None of these values are secrets: the Cloudflare account ID and the Supabase project
# ref/URL are identifiers, not credentials. Secrets (tokens, DB password) are never output.

output "r2_bucket_name" {
  description = "Name of the R2 bucket."
  value       = cloudflare_r2_bucket.lake.name
}

output "r2_s3_endpoint" {
  description = "S3 API endpoint for R2 in this account (use as S3_ENDPOINT, path-style, region auto)."
  value       = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
}

output "supabase_project_ref" {
  description = "Supabase project reference ID."
  value       = supabase_project.wanderlake.id
}

output "supabase_api_url" {
  description = "Supabase API URL for the project."
  value       = "https://${supabase_project.wanderlake.id}.supabase.co"
}

output "web_production_url" {
  description = "Production URL of the website (live after the first deploy from main)."
  value       = "https://${vercel_project_domain.production.domain}"
}
