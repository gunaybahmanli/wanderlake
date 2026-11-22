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
| `REST_COUNTRIES_API_KEY` | `rest_countries` DAG (Airflow) | root `.env` | GitHub Actions secret (when a cloud job runs it) |
| `CLOUD_S3_ACCESS_KEY`, `CLOUD_S3_SECRET_KEY` | publish job: R2 Parquet (`CLOUD_S3_BUCKET`) | root `.env` | GitHub Actions secret (when a cloud job runs it) |
| `SUPABASE_DB_HOST`, `_PORT`, `_USER`, `_NAME` | publish job: Supabase session pooler (password: `TF_VAR_supabase_db_password`) | root `.env` | GitHub Actions variables (not secrets) |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | website (server side), `make web-env`, `make api-check` | root `.env` | Vercel environment variables via OpenTofu (key write-only, not in state; read-only by RLS) |

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
buckets but cannot read or write objects through the S3 API. The publish job uses a separate
**R2 publish token** (below).

### Cloudflare R2 publish token (S3 credentials for the publish job)

The publish job (`publish_cloud`, `make publish`) writes Parquet to the bucket through R2's
S3 API. It gets its own credentials, limited to that bucket and to objects:

1. Dashboard: **R2 Object Storage > Manage API tokens > Create API token** (an *Account* token,
   so it keeps working if your user changes; a *User* token is fine for a personal project).
2. Permissions: **Object Read & Write** (not Admin: it cannot create or delete buckets or change
   their settings).
3. Specify bucket(s): **Apply to specific buckets only > `wanderlake-lake`**.
4. TTL: an expiry date (recommended: 90 days; note it in the expiry log). Client IP filtering:
   none (the laptop's IP changes).
5. Create, then copy the **Access Key ID** and **Secret Access Key** (shown once; the S3
   credentials, not the token value) into `CLOUD_S3_ACCESS_KEY` and `CLOUD_S3_SECRET_KEY`.
   `CLOUD_S3_BUCKET=wanderlake-lake`.

The endpoint is not a secret and is not in `.env`: compose builds
`https://<TF_VAR_cloudflare_account_id>.r2.cloudflarestorage.com` (also `tofu output
r2_s3_endpoint`), with region `auto` and path-style addressing. The local `S3_*` variables
stay for SeaweedFS; nothing in the publish job reads them.

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

### Supabase database connection (publish job) and REST API

The publish job connects to Postgres through Supabase's **session pooler** (Supavisor):
dashboard **Connect > Session pooler**.

| Variable | Value |
|---|---|
| `SUPABASE_DB_HOST` | `aws-0-eu-central-1.pooler.supabase.com` (the host shown there) |
| `SUPABASE_DB_PORT` | `5432` (session mode; `6543` is transaction mode) |
| `SUPABASE_DB_USER` | `postgres.<project ref>` (the pooler routes on the ref) |
| `SUPABASE_DB_NAME` | `postgres` |
| password | `TF_VAR_supabase_db_password` (compose passes it as `SUPABASE_DB_PASSWORD`) |

- **Why the session pooler:** the direct host (`db.<ref>.supabase.co`) is IPv6-only on the
  free plan, which Docker on WSL does not route by default. Session mode gives the client one
  server connection for its whole session, which the job needs for temporary tables and `COPY`;
  transaction mode (port 6543) would not keep them.
- TLS is required (`sslmode=require`). The job writes as `postgres`, because it also applies
  the migrations in `supabase/migrations/` (ADR-0010 lists a dedicated role as a follow-up).
- **REST API:** `supabase_settings.wanderlake` (OpenTofu) exposes the schema `api` to PostgREST
  (`db_schema = public,graphql_public,api`). `SUPABASE_URL` (`https://<ref>.supabase.co`) and
  `SUPABASE_ANON_KEY` (Project settings > API keys, the `anon` / publishable key) are what the
  website uses. The anon key is public by design; RLS and grants make it read-only, and
  `make api-check` proves it. The `service_role` / secret key bypasses RLS: never put it in
  `.env`, the website or CI.

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

### REST Countries (source API)

REST Countries v1-v4 (`restcountries.com/v3.1/...`) are deprecated; v5 needs an API key.

1. Create a free account at https://restcountries.com (Free plan: $0, 1,000 requests a month,
   2 API keys, data synced every 24 h, no credit card).
2. Create an API key on the API Keys page and store it as `REST_COUNTRIES_API_KEY` in `.env`.
   Compose passes it to the Airflow container only.

Usage: one weekly snapshot is 3 requests (100 records per request on the free plan), about
13 requests a month. The rate limit is 20 requests per 10 seconds.

### Token expiry log

Update this table whenever a token is created or rotated.

| Token | Created | Expires | Rotated by |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | YYYY-MM-DD | YYYY-MM-DD | |
| `SUPABASE_ACCESS_TOKEN` | YYYY-MM-DD | YYYY-MM-DD (created + 90 days) | |
| `VERCEL_API_TOKEN` | YYYY-MM-DD | YYYY-MM-DD (created + 90 days) | |
| `REST_COUNTRIES_API_KEY` | 2026-11-19 | see the API Keys page (revoke there) | |
| R2 publish token (`CLOUD_S3_*`) | YYYY-MM-DD | YYYY-MM-DD (TTL set at creation) | |

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

`supabase/supabase` and `vercel/vercel` are installed without signature verification. The risk,
mitigations and review rules are recorded in
[ADR-0006](../adr/0006-accept-unsigned-opentofu-providers.md). In practice: before bumping either
provider version, read its release notes and source tag, and commit the updated
`.terraform.lock.hcl` in the same change.

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

### REST Countries API key

1. Create a second key on the API Keys page (the free plan allows 2).
2. Replace `REST_COUNTRIES_API_KEY` in `.env`, then recreate Airflow (`make mode-ingest`).
3. Trigger `rest_countries` once and check that both tasks succeed.
4. Revoke the old key. Update the expiry log.

### R2 publish token (before its TTL ends)

1. Create a new R2 API token exactly as above (Object Read & Write, bucket `wanderlake-lake`
   only, new TTL).
2. Replace `CLOUD_S3_ACCESS_KEY` and `CLOUD_S3_SECRET_KEY` in `.env`, then recreate Airflow so
   it sees them (`make mode-full`, or `make mode-ingest`).
3. Run `make publish`: every table must show `ok yes`.
4. Delete the old token under R2 > Manage API tokens. Update the expiry log.

An expired or revoked token fails the publish with `AccessDenied` / `InvalidAccessKeyId` on the
first `s3.put`; Supabase is not touched for that table.

### Supabase database password (also the publish job's pooler password)

1. Reset it in the Supabase dashboard (Project settings > Database), using the rules above.
   The pooler accepts the new password within a minute or two.
2. Put the same value in `TF_VAR_supabase_db_password` in `.env`, then recreate Airflow
   (`make mode-full` or `make mode-ingest`), which passes it as `SUPABASE_DB_PASSWORD`.
3. Run `make publish` (must end with every table `ok yes`), then `make tofu-plan` and check what
   it proposes for `database_password` before applying.
4. Update every other client that connects with it (later: GitHub secrets).

The pooler host, port, user and database name are not secrets and do not change on rotation.
The anon key changes only if the project's API keys are rotated (Project settings > API keys);
then update `SUPABASE_ANON_KEY` in `.env`, bump `supabase_anon_key_version` in
`infra/tofu/variables.tf` (the Vercel value is write-only, so OpenTofu re-sends it only when the
version changes), run `make tofu-plan` and `make tofu-apply`, redeploy the website, then run
`make web-env` and `make api-check`.

### State passphrase

Changing it needs a key rollover, otherwise the existing state cannot be read:

1. Add a `fallback` block for the old key in the `encryption` block of `versions.tf`, as in the
   [OpenTofu docs](https://opentofu.org/docs/language/state/encryption/#key-and-method-rollover),
   with the old passphrase taken from a temporary variable (e.g. `TF_VAR_state_passphrase_old`).
2. Set the new `TF_VAR_state_passphrase` and rewrite the state with the new key:
   `cd infra/tofu && set -a && . ../../.env && set +a && tofu apply -refresh-only`.
   Confirm with `make tofu-plan` (no changes).
3. Remove the fallback block and the old variable. Update the password manager entry.
