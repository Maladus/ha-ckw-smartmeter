#!/usr/bin/env bash
#
# onboard.sh - complete Home Assistant's onboarding through the API (#40).
#
#   mise run dev-onboard
#
# A fresh config/ starts in onboarding: no owner, no core config, and no token
# that a script could use. This script walks the four onboarding steps
# (users -> core_config -> analytics -> integration) over HTTP, exchanges the
# auth code at /auth/token, and writes the access token to .dev/token so that
# later tasks can call the API without a browser.
#
# It is a no-op once config/.storage/onboarding exists, and it refuses to guess
# when onboarding was left half-done: `mise run dev-reset -- --full` gives a
# clean store and this script a clean run.
#
# The credentials below are local dev defaults, overridable through the
# environment (DEV_USERNAME, DEV_PASSWORD, DEV_USER_NAME, DEV_LANGUAGE, and
# HA_BASE_URL for a non-default host/port).

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

readonly BASE_URL="${HA_BASE_URL:-http://localhost:8123}"
readonly ONBOARDING_STORE="$REPO_ROOT/config/.storage/onboarding"
readonly TOKEN_FILE="$REPO_ROOT/.dev/token"
readonly READY_TIMEOUT_SECONDS=120

readonly USER_NAME="${DEV_USER_NAME:-CKW Dev}"
readonly USERNAME="${DEV_USERNAME:-ckwdev}"
readonly PASSWORD="${DEV_PASSWORD:-ckw-dev-password}"
readonly LANGUAGE="${DEV_LANGUAGE:-en}"

fail() {
    printf 'dev-onboard: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'dev-onboard: %s\n' "$*"
}

command -v python3 >/dev/null 2>&1 ||
    fail "python3 is not on PATH - run this through mise (mise run dev-onboard)"

# call LABEL CURL_ARGS... - run curl, print the body, fail on a non-2xx status.
# The status is appended as the last line and split off again, so a failure
# reports the body Home Assistant actually sent instead of a bare "curl 22".
call() {
    local label=$1
    shift
    local response status body
    response=$(curl --silent --show-error -w $'\n%{http_code}' "$@") ||
        fail "$label: curl failed"
    status=${response##*$'\n'}
    body=${response%$'\n'*}
    [[ $status == 2* ]] || fail "$label answered $status: $body"
    printf '%s' "$body"
}

# json KEY VALUE... - a JSON object from alternating key/value arguments.
json() {
    python3 -c 'import json, sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"
}

# json_field KEY - the value of a top-level key of the JSON on stdin.
json_field() {
    python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

if [[ -f "$ONBOARDING_STORE" ]]; then
    info "already onboarded ($ONBOARDING_STORE exists) - nothing to do"
    info "start over with: mise run dev-reset -- --full"
    exit 0
fi

info "waiting for $BASE_URL/api/onboarding"
deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
until steps=$(curl --silent --fail "$BASE_URL/api/onboarding" 2>/dev/null); do
    if ((SECONDS >= deadline)); then
        fail "Home Assistant did not answer $BASE_URL/api/onboarding within ${READY_TIMEOUT_SECONDS}s - start it with: mise run dev-up"
    fi
    sleep 2
done

# Partial state is not recoverable without an existing credential: the user step
# can be created only once, and only its response carries the auth code.
done_steps=$(printf '%s' "$steps" | python3 -c '
import json, sys
print(" ".join(step["step"] for step in json.load(sys.stdin) if step["done"]))
')
[[ -z $done_steps ]] ||
    fail "onboarding is partly done (${done_steps}) but $ONBOARDING_STORE is missing - run: mise run dev-reset -- --full"

# The OAuth client id during onboarding is the instance URL - that is what the
# frontend posts, and what /auth/token accepts. /api/onboarding in current Home
# Assistant only lists the steps, so the response is consulted first (a future
# version may include it) and the instance URL is the documented fallback.
client_id=$(printf '%s' "$steps" | python3 -c '
import json, sys
data = json.load(sys.stdin)
print(data.get("client_id", "") if isinstance(data, dict) else "")
')
[[ -n $client_id ]] || client_id="$BASE_URL/"

info "creating the owner user $USERNAME"
user_payload=$(json \
    name "$USER_NAME" \
    username "$USERNAME" \
    password "$PASSWORD" \
    client_id "$client_id" \
    language "$LANGUAGE")
created=$(call "POST /api/onboarding/users" \
    -X POST \
    -H 'Content-Type: application/json' \
    -d "$user_payload" \
    "$BASE_URL/api/onboarding/users")

auth_code=$(printf '%s' "$created" | json_field auth_code)
[[ -n $auth_code ]] || fail "POST /api/onboarding/users returned no auth_code: $created"

# The auth code is single use and names the client id it was issued for.
info "exchanging the auth code at /auth/token"
token_response=$(call "POST /auth/token" \
    -X POST \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode grant_type=authorization_code \
    --data-urlencode code="$auth_code" \
    --data-urlencode client_id="$client_id" \
    "$BASE_URL/auth/token")

access_token=$(printf '%s' "$token_response" | json_field access_token)
[[ -n $access_token ]] || fail "POST /auth/token returned no access_token: $token_response"

# The remaining steps need an authenticated request; core_config and analytics
# take no body, integration needs the client id and a redirect URI on the same
# origin (indieauth.verify_redirect_uri).
info "finishing core config, analytics and integration"
call "POST /api/onboarding/core_config" \
    -X POST \
    -H "Authorization: Bearer $access_token" \
    "$BASE_URL/api/onboarding/core_config" >/dev/null

call "POST /api/onboarding/analytics" \
    -X POST \
    -H "Authorization: Bearer $access_token" \
    "$BASE_URL/api/onboarding/analytics" >/dev/null

integration_payload=$(json \
    client_id "$client_id" \
    redirect_uri "$BASE_URL/?auth_callback=1")
call "POST /api/onboarding/integration" \
    -X POST \
    -H "Authorization: Bearer $access_token" \
    -H 'Content-Type: application/json' \
    -d "$integration_payload" \
    "$BASE_URL/api/onboarding/integration" >/dev/null

mkdir -p "$REPO_ROOT/.dev"
(umask 077 && printf '%s\n' "$access_token" >"$TOKEN_FILE")

[[ -f "$ONBOARDING_STORE" ]] ||
    info "warning: $ONBOARDING_STORE is not on disk yet - the instance may still be writing it"

info "onboarding complete"
info "UI: $BASE_URL  (user: $USERNAME  password: $PASSWORD)"
info "access token: .dev/token (gitignored, invalid after: mise run dev-reset -- --full)"
info "next: mise run dev-smoke"
