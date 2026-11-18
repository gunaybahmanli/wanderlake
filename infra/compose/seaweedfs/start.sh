#!/bin/sh
# Render the SeaweedFS S3 identity config from environment variables,
# then hand off to the image's own entrypoint (which drops to the seaweed user).
set -eu

: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

umask 077
cat > /tmp/s3.json <<EOF
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
chown seaweed:seaweed /tmp/s3.json

exec /entrypoint.sh "$@"
