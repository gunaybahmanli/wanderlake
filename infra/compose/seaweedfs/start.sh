#!/bin/sh
# Render the SeaweedFS S3 identity config from environment variables,
# then hand off to the image's own entrypoint (which drops to the seaweed user).
set -eu

: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

# Written to /etc/seaweedfs, not /tmp: with fs.protected_regular=2 (enabled on WSL and many
# distros) even root cannot reopen an existing file owned by another user in sticky /tmp, so a
# container restart would fail with "Permission denied". Owner root, readable by group seaweed.
CONFIG=/etc/seaweedfs/s3.json
umask 027
cat > "$CONFIG" <<EOF
{
  "identities": [
    {
      "name": "admin",
      "credentials": [
        { "accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}" }
      ],
      "actions": ["Admin", "Read", "List", "Tagging", "Write"]
    }
  ]
}
EOF
chown root:seaweed "$CONFIG"
chmod 640 "$CONFIG"

exec /entrypoint.sh "$@"
