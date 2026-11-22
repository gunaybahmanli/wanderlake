#!/usr/bin/env bash
# Prove that the public REST API is read-only for the anon key (make api-check).
# Needs SUPABASE_URL and SUPABASE_ANON_KEY in the environment (the Makefile loads .env).
# Prints HTTP status codes and row counts only, never the key.
set -euo pipefail

: "${SUPABASE_URL:?set SUPABASE_URL in .env}"
: "${SUPABASE_ANON_KEY:?set SUPABASE_ANON_KEY in .env}"
API="${SUPABASE_URL%/}/rest/v1"
BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT
FAILED=0

# request <method> <profile header> <path> [data]  -> prints the HTTP status
request() {
  local method=$1 profile=$2 path=$3 data=${4:-}
  curl -sS -o "$BODY" -w '%{http_code}' -X "$method" "$API/$path" \
    -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
    -H "$profile" -H 'Content-Type: application/json' -H 'Prefer: return=minimal' \
    ${data:+--data "$data"}
}

expect() {  # expect <label> <status> <allowed statuses...>
  local label=$1 status=$2; shift 2
  local detail
  detail=$(python3 -c 'import json,sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print(""); sys.exit()
if isinstance(d, list):
    print(f"{len(d)} rows")
else:
    print(" ".join(str(d.get(k)) for k in ("code", "message") if d.get(k)))' "$BODY")
  if [[ " $* " == *" $status "* ]]; then
    printf 'ok    %-52s HTTP %s  %s\n' "$label" "$status" "$detail"
  else
    printf 'FAIL  %-52s HTTP %s (want %s)  %s\n' "$label" "$status" "$*" "$detail"
    FAILED=1
  fi
}

READ='Accept-Profile: api'
WRITE='Content-Profile: api'

status=$(request GET "$READ" 'city_overview?select=city_id,name,rate_to_eur')
expect "anon SELECT api.city_overview" "$status" 200
before=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$BODY")
status=$(request POST "$WRITE" 'city_overview' '{"city_id":"zz-api-check","rate_to_eur":1,"rate_to_usd":1,"rate_to_azn":1}')
expect "anon INSERT api.city_overview is rejected" "$status" 401 403
status=$(request PATCH "$WRITE" 'city_overview?city_id=eq.baku' '{"name":"changed"}')
expect "anon UPDATE api.city_overview is rejected" "$status" 401 403
status=$(request DELETE "$WRITE" 'city_overview?city_id=eq.baku')
expect "anon DELETE api.city_overview is rejected" "$status" 401 403
status=$(request GET "Accept-Profile: wl_meta" 'schema_migrations')
expect "schema wl_meta is not exposed" "$status" 406
status=$(request GET "$READ" 'city_overview?select=city_id')
expect "api.city_overview unchanged afterwards" "$status" 200
after=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$BODY")
if [[ "$before" != "$after" ]]; then
  echo "FAIL  row count changed: $before -> $after"; FAILED=1
fi

exit $FAILED
