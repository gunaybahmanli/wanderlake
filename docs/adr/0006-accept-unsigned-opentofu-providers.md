# ADR-0006: Accept unsigned OpenTofu providers (Supabase, Vercel)

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

OpenTofu verifies a provider's signature when the registry publishes the provider's GPG key. For
`supabase/supabase` and `vercel/vercel`, the OpenTofu registry has no key, and `tofu init` prints
"Signature validation was skipped due to the registry not containing GPG keys for this provider".
`cloudflare/cloudflare` is signed and verified.

Without a signature, a compromised or tampered release could not be detected that way. These
providers run with tokens that can create and change cloud resources.

## Decision

Use the unsigned providers, with these mitigations:

- **Exact version pins** in `infra/tofu/versions.tf`: `supabase/supabase` 1.11.1 and `vercel/vercel` 5.27.0.
- **Checksums pinned** in the committed `infra/tofu/.terraform.lock.hcl`. Every later `tofu init`,
  locally and in CI, must download binaries with the same checksums, or it fails.
- **Review on every upgrade:** read the release notes and check the source tag on GitHub before
  bumping a version, and commit the updated lock file in the same change.
- **Re-check on every upgrade** whether the registry now publishes a key; if it does, this ADR is
  no longer needed and is marked Deprecated.
- **Least-privilege tokens limit the damage:** the Supabase token is scoped to one organization and
  the Vercel token to one team, both with a 90-day expiry.

## Alternatives considered

- **Do not use these providers; manage Supabase and Vercel manually or with scripts:** gives up
  reviewable, reproducible infrastructure for both services.
- **Install the providers from a local mirror with self-verified binaries:** more assurance, but a
  maintenance burden that is out of proportion for a single-person project.
- **Wait until the registry publishes keys:** blocks managing Supabase and Vercel in code for an
  unknown time.

## Consequences

- Supabase and Vercel stay in code, with the same plan-review-apply workflow as Cloudflare.
- The first download of each pinned version is trusted on first use; later downloads are verified
  against the pinned checksums.
- Provider upgrades need a deliberate review step.
- The operational steps are in the cloud runbook,
  [docs/runbooks/cloud-setup.md](../runbooks/cloud-setup.md#accepted-risk-unsigned-supabase-and-vercel-providers).
