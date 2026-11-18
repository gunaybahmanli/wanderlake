terraform {
  required_version = ">= 1.13.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.29.0"
    }
    supabase = {
      source  = "supabase/supabase"
      version = "1.11.1"
    }
    vercel = {
      source  = "vercel/vercel"
      version = "5.27.0"
    }
  }

  # State: local file (terraform.tfstate, gitignored) for now.

  # Native OpenTofu encryption: state and saved plan files are encrypted at rest with
  # AES-GCM, using a key derived (PBKDF2) from TF_VAR_state_passphrase in the root .env.
  # Losing the passphrase means losing access to the state. See docs/runbooks/cloud-setup.md.
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
      # Stable name stored in the encrypted file, so the key provider can be renamed later.
      encrypted_metadata_alias = "wanderlake-state"
    }

    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }

    state {
      method   = method.aes_gcm.state
      enforced = true # never write unencrypted state
    }

    plan {
      method   = method.aes_gcm.state
      enforced = true # saved plans contain secrets too
    }
  }
}
