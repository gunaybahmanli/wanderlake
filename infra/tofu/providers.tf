# Credentials come from the environment (loaded from the root .env by the Makefile),
# so they never appear in code or in state:
#   CLOUDFLARE_API_TOKEN   -> cloudflare provider
#   SUPABASE_ACCESS_TOKEN  -> supabase provider
#   VERCEL_API_TOKEN       -> vercel provider (token is scoped to the Hobby team)

provider "cloudflare" {}

provider "supabase" {}

provider "vercel" {
  # Set explicitly (not only via the token's default team): vercel provider 5.27.0 otherwise
  # stores an empty team_id in the resource identity at create time and then fails every
  # later plan with "Unexpected Identity Change".
  team = var.vercel_team
}
