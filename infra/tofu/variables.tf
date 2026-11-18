# Values without defaults come from TF_VAR_* variables in the root .env.

variable "state_passphrase" {
  description = "Passphrase for OpenTofu state and plan encryption (TF_VAR_state_passphrase)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "state_passphrase must be at least 16 characters."
  }
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID that owns the R2 bucket (TF_VAR_cloudflare_account_id)."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.cloudflare_account_id))
    error_message = "cloudflare_account_id must be a 32-character hex string."
  }
}

variable "supabase_org_id" {
  description = "Supabase organization slug (TF_VAR_supabase_org_id)."
  type        = string
}

variable "supabase_db_password" {
  description = "Postgres password for the Supabase project (TF_VAR_supabase_db_password)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.supabase_db_password) >= 12
    error_message = "supabase_db_password must be at least 12 characters."
  }
}

variable "r2_bucket_name" {
  description = "Name of the R2 bucket for the cloud lakehouse."
  type        = string
  default     = "wanderlake-lake"
}

variable "r2_location" {
  description = "R2 location hint (apac, eeur, enam, weur, wnam, oc). Only applied at creation."
  type        = string
  default     = "eeur"
}

variable "supabase_project_name" {
  description = "Name of the Supabase project."
  type        = string
  default     = "wanderlake"
}

variable "supabase_region" {
  description = "AWS region of the Supabase project."
  type        = string
  default     = "eu-central-1"
}

variable "vercel_project_name" {
  description = "Name of the Vercel project for the website."
  type        = string
  default     = "wanderlake"
}

variable "web_domain" {
  description = "Production domain of the website on Vercel."
  type        = string
  default     = "wanderlake.vercel.app"
}

variable "vercel_team" {
  description = "Vercel team slug or ID that owns the project (an identifier, not a secret)."
  type        = string
  default     = "me-f548"
}
