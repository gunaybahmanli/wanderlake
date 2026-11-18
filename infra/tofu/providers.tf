# Credentials come from the environment (loaded from the root .env by the Makefile),
# so they never appear in code or in state:
#   CLOUDFLARE_API_TOKEN   -> cloudflare provider
#   SUPABASE_ACCESS_TOKEN  -> supabase provider

provider "cloudflare" {}

provider "supabase" {}
