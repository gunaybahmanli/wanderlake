# ADR-0005: Use OpenTofu for infrastructure as code

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

The always-on version uses resources on three providers: a Cloudflare R2 bucket, a Supabase project
and a Vercel project. They should be reproducible, reviewable in pull requests, and safe against
mistakes, instead of being clicked together in web consoles. The state of these resources includes
a secret (the Supabase database password).

## Decision

Manage cloud resources with OpenTofu (MPL-2.0), pinned to 1.13.9 locally and in CI, in `infra/tofu`:

- Providers are pinned exactly (`cloudflare/cloudflare` 5.29.0, `supabase/supabase` 1.11.1,
  `vercel/vercel` 5.27.0) and `.terraform.lock.hcl` is committed.
- Credentials come only from environment variables loaded from the root `.env` by the Makefile;
  they never appear in code or state.
- Native state encryption is enabled and enforced for state and saved plan files: `pbkdf2` key
  provider (passphrase from `TF_VAR_state_passphrase`) with the `aes_gcm` method.
- The workflow is plan, review, apply: `make tofu-plan` saves an encrypted plan, and
  `make tofu-apply` applies exactly that plan file, then deletes it.
- Resources that hold data (the R2 bucket and the Supabase project) have `prevent_destroy = true`.
- State is local for now (gitignored and encrypted).
- The CI job "OpenTofu checks" runs `tofu fmt -check` and `tofu validate` without any credentials.

## Alternatives considered

- **Terraform:** the same language and provider ecosystem, but licensed under the Business Source
  License since version 1.6, which is not open source. It also has no built-in client-side state
  encryption.
- **Pulumi:** infrastructure in a general-purpose language, but needs either Pulumi Cloud or a
  self-managed state backend, and HCL is the more common skill for this kind of work.
- **Manual console setup:** fastest once, but not reproducible, not reviewable, and easy to drift.

## Consequences

- Every cloud change is a reviewed diff with a plan, and data resources cannot be destroyed by accident.
- The encrypted state can only be read with the passphrase. **Losing it means losing access to the
  state;** recovery is a rebuild with `tofu import` (see the cloud runbook).
- Local state works for one person on one machine. Running plans from CI or a second machine will
  need a remote backend, recorded in a new ADR.
- Provider maturity varies: two providers are unsigned in the OpenTofu registry
  (see [ADR-0006](0006-accept-unsigned-opentofu-providers.md)), and the Vercel provider needed two
  workarounds (the auto-assigned `.vercel.app` domain, and an explicit `team` setting), documented in
  the runbook.
