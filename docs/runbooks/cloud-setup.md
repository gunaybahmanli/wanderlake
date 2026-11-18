# Runbook: cloud accounts, tokens and OpenTofu

The always-on version of Wanderlake runs on free tiers. Cloud resources are managed with
OpenTofu in [`infra/tofu`](../../infra/tofu):

| Provider | Resource | Free tier notes |
|---|---|---|
| Cloudflare R2 | bucket `wanderlake-lake` (location hint `eeur`) | 10 GB storage, 1M writes and 10M reads per month, no egress fees |
| Supabase | project `wanderlake` in `eu-central-1` | 2 free projects per org; 500 MB database; **paused after 7 days without activity** |
| Vercel | project `wanderlake` (Next.js in `web/`), domain `wanderlake.vercel.app` | Hobby plan: personal, non-commercial use; build minutes and bandwidth limits apply |

## Where secrets live

| Variable | Used by | Local | CI / later |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare provider | root `.env` | GitHub Actions secret |
| `TF_VAR_cloudflare_account_id` | R2 bucket, endpoint | root `.env` | GitHub Actions variable (not a secret) |
| `SUPABASE_ACCESS_TOKEN` | Supabase provider | root `.env` | GitHub Actions secret |
| `TF_VAR_supabase_org_id` | Supabase project | root `.env` | GitHub Actions variable (not a secret) |
| `TF_VAR_supabase_db_password` | Supabase database | root `.env` | GitHub Actions secret |
| `TF_VAR_state_passphrase` | OpenTofu state encryption | root `.env` **and your password manager** | GitHub Actions secret |
| `VERCEL_API_TOKEN` | Vercel provider | root `.env` | GitHub Actions secret |

Rules:

- `.env` is gitignored. Never commit it, paste it into issues, or print values in CI logs.
- `.env.example` lists every variable with placeholder values only.
- The Makefile loads `.env` through the shell (`set -a; . ./.env`). Avoid `$`, backticks, `\` and
  quotes in values, or they will be changed when loaded.
- The CI job "OpenTofu checks" runs `fmt -check` and `validate` only and needs **no** secrets.
  Secrets go to GitHub only when CI starts running `plan`/`apply` (a later step).

## How the accounts and tokens were created

### Cloudflare (R2)

1. Create a Cloudflare account and enable R2 in the dashboard (**R2 Object Storage**). Enabling R2
   requires accepting the R2 terms, even on the free tier.
2. Copy the **Account ID** from the dashboard sidebar into `TF_VAR_cloudflare_account_id`.
3. Create an API token: **My Profile > API Tokens > Create Token > Create Custom Token**.
   - Permissions: **Account > Workers R2 Storage > Edit**
   - Account resources: **Include > (this account only)**
   - TTL: set an expiry (recommended: 90 days) and note the date below.
4. Store it as `CLOUDFLARE_API_TOKEN`.

This is a user API token (verified with `GET /client/v4/user/tokens/verify`). It can manage R2
buckets but cannot read or write objects through the S3 API. Applications get separate
**R2 S3 access keys** (access key ID + secret) in a later step; they map to the generic
`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` and `S3_REGION=auto` variables.

### Supabase

1. Create a Supabase account and an organization on the **Free** plan.
2. Copy the organization slug (Organization settings, or the dashboard URL) into
   `TF_VAR_supabase_org_id`.
3. Create a **fine-grained organization-scoped access token** (Account > Access Tokens):
   - Organization access: **this organization only**
   - Permission preset: **read/write**
   - Expiry: **90 days**
4. Store it as `SUPABASE_ACCESS_TOKEN`.

This is not a legacy personal access token: it cannot reach other organizations, and it expires
on its own after 90 days. When it expires, `make tofu-plan` fails with an authentication error;
rotate it as described below.

5. Generate a database password (at least 12 characters, letters, digits and symbols, but none
   of `$ \` \ " '`) and store it as `TF_VAR_supabase_db_password`.

### Vercel

1. Create a Vercel account (Hobby plan) and install the **Vercel GitHub app** on the repository
   `gunaybahmanli/wanderlake` (GitHub > Settings > Applications, repository access: this repo only).
   OpenTofu can only link the project to a repository the app can see.
2. Create an access token: **Account Settings > Tokens > Create**:
   - Scope: **the Hobby team `me-f548` only**
   - Expiration: **90 days**
3. Store it as `VERCEL_API_TOKEN`.

The token's scope is also its default team, so the provider needs no `team` setting. It can manage
projects, domains and deployments in that team only. Deployments themselves do not use the token:
Vercel builds from GitHub pushes through the GitHub app.

### Token expiry log

Update this table whenever a token is created or rotated.

| Token | Created | Expires | Rotated by |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | YYYY-MM-DD | YYYY-MM-DD | |
| `SUPABASE_ACCESS_TOKEN` | YYYY-MM-DD | YYYY-MM-DD (created + 90 days) | |
| `VERCEL_API_TOKEN` | YYYY-MM-DD | YYYY-MM-DD (created + 90 days) | |

## OpenTofu state and its encryption

- State is local for now: `infra/tofu/terraform.tfstate` (gitignored), plus `.backup`.
- State and saved plan files are **encrypted at rest** with OpenTofu's native encryption
  (`encryption` block in `infra/tofu/versions.tf`): AES-GCM with a key derived via PBKDF2 from
  `TF_VAR_state_passphrase`. Encryption is enforced, so OpenTofu refuses to write plain-text state.
- The state contains the Supabase database password. That is why it is encrypted.

> **Losing `TF_VAR_state_passphrase` means losing access to the state.** There is no recovery:
> OpenTofu cannot read the file without it. Keep a copy in your password manager.
> If it is lost anyway, delete the unreadable state and rebuild it with `tofu import` (below).
> The cloud resources themselves are not affected.

### Daily use

```bash
make tofu-init      # once, and after provider version changes
make tofu-plan      # review; saves an encrypted plan file (wanderlake.tfplan)
make tofu-apply     # applies exactly that saved plan, then deletes it
```

The R2 bucket and the Supabase project have `prevent_destroy = true` (they hold data). The Vercel resources can be recreated safely. To intentionally delete one, remove that line in a
reviewed change first.

### Accepted risk: unsigned Supabase and Vercel providers

| | |
|---|---|
| Risk | `supabase/supabase` and `vercel/vercel` are installed **without signature verification**: the OpenTofu registry has no GPG keys for them (`tofu init` prints "Signature validation was skipped"). A compromised release could not be detected by signature. |
| Mitigation | The provider versions are pinned exactly (supabase `1.11.1`, vercel `5.27.0`), and their checksums are pinned in the committed `infra/tofu/.terraform.lock.hcl`. Every later `tofu init` (locally and in CI) must download a binary with the same checksum, or it fails. `cloudflare/cloudflare` is signed and verified. |
| On upgrade | Review the provider release notes and source tag on GitHub before bumping the version, then commit the updated lock file in the same change. |
| Status | Supabase accepted on 2026-11-18, Vercel accepted on 2026-11-18. Re-check on each upgrade whether the registry now publishes a key. |

### Known Vercel provider quirks (5.27.0)

- **`<project>.vercel.app` is added automatically.** Creating a project makes Vercel attach
  `<name>.vercel.app` to it. Creating `vercel_project_domain` for that same name then fails with
  `duplicate-team-registration`. Fix: import it instead,
  `tofu import vercel_project_domain.production <project_id>/wanderlake.vercel.app`.
- **Set the team on the provider.** Without `team`, the provider stores an empty `team_id` in the
  resource identity at create time, and every later plan fails with "Unexpected Identity Change".
  `providers.tf` sets `team = var.vercel_team` (`me-f548`). Import projects with the team prefix:
  `tofu import vercel_project.wanderlake <team_id>/<project_id>` (IDs: Vercel team and project
  settings). `tofu state rm` before a re-import only forgets the resource; it never deletes it.

IDs for this project: team `team_cJmftc3cZvS2wgjzWctXmp4V`, project `prj_bbAJKDa9avJLDAgClTwRhtNBi2Yr`
(identifiers, not secrets).

### Rebuilding lost state

```bash
cd infra/tofu && set -a && . ../../.env && set +a
tofu import cloudflare_r2_bucket.lake "$TF_VAR_cloudflare_account_id/wanderlake-lake/default"
tofu import supabase_project.wanderlake trknxrrenqikqmzmdavi   # project ref (also in the dashboard URL)
tofu import vercel_project.wanderlake <team_id>/<project_id>   # include the team (see below)
tofu import vercel_project_domain.production <project_id>/wanderlake.vercel.app
tofu plan                                               # should show no changes
```

## Rotating a token or secret

General rule: **create the new credential, switch to it, verify, then revoke the old one.**

### Cloudflare API token

1. Create a new token with the same permissions (above), or use **Roll** on the existing token.
2. Replace `CLOUDFLARE_API_TOKEN` in `.env` (and the GitHub secret, once it exists).
3. Run `make tofu-plan`: it must succeed with "No changes".
4. Delete the old token in the dashboard. Update the expiry log.

### Supabase access token (every 90 days, before it expires)

1. Create a new fine-grained token: this organization only, read/write, 90-day expiry.
2. Replace `SUPABASE_ACCESS_TOKEN` in `.env` (and the GitHub secret, once it exists).
3. Run `make tofu-plan`: it must succeed with "No changes".
4. Revoke the old token. Update the expiry log, and set a calendar reminder ~2 weeks before the
   new expiry date.

### Vercel access token (every 90 days, before it expires)

1. Create a new token: scope the Hobby team `me-f548` only, 90-day expiry.
2. Replace `VERCEL_API_TOKEN` in `.env` (and the GitHub secret, once it exists).
3. Run `make tofu-plan`: it must succeed with "No changes".
4. Delete the old token in Account Settings > Tokens. Update the expiry log.

Deployments keep working during rotation: they come from the GitHub app, not the token.

### Supabase database password

1. Reset it in the Supabase dashboard (Project settings > Database), using the rules above.
2. Put the same value in `TF_VAR_supabase_db_password` in `.env`.
3. Run `make tofu-plan` and check what it proposes for `database_password` before applying.
4. Update every client that connects with it (later: API, GitHub secrets).

### State passphrase

Changing it needs a key rollover, otherwise the existing state cannot be read:

1. Add a `fallback` block for the old key in the `encryption` block of `versions.tf`, as in the
   [OpenTofu docs](https://opentofu.org/docs/language/state/encryption/#key-and-method-rollover),
   with the old passphrase taken from a temporary variable (e.g. `TF_VAR_state_passphrase_old`).
2. Set the new `TF_VAR_state_passphrase` and rewrite the state with the new key:
   `cd infra/tofu && set -a && . ../../.env && set +a && tofu apply -refresh-only`.
   Confirm with `make tofu-plan` (no changes).
3. Remove the fallback block and the old variable. Update the password manager entry.
