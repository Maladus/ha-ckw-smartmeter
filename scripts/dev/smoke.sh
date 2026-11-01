#!/usr/bin/env bash
#
# smoke.sh - prove the dev instance is healthy (gate G-6, #42).
#
#   mise run dev-smoke
#
# Asserts, in this order:
#   1. http://localhost:8123/manifest.json answers 200
#   2. the component is visible inside the container, not only on the host
#      (catches a dropped or mislabeled bind mount, e.g. a missing `:Z`)
#   3. the container log has no Python traceback mentioning custom_components.ckw
#   4. the effective `ckw` domain version matches const.py
#
# Every failure prints one `smoke: <reason>` line and exits non-zero, so the
# gate can be read from a CI log without the surrounding prose.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

readonly CONTAINER="ha-dev"
readonly HA_URL="http://localhost:8123"
readonly COMPONENT_MANIFEST="/config/custom_components/ckw/manifest.json"

fail() {
    printf 'smoke: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'smoke: %s\n' "$*"
}

[[ $(podman inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true) == "true" ]] ||
    fail "container $CONTAINER is not running - start it with: mise run dev-up"

# --- 1: the frontend answers ------------------------------------------------
status=$(curl -s -o /dev/null -w '%{http_code}' "$HA_URL/manifest.json" || true)
[[ $status == "200" ]] ||
    fail "$HA_URL/manifest.json answered ${status:-nothing}, expected 200"

# --- 2: the component is inside the container -------------------------------
manifest=$(podman exec "$CONTAINER" cat "$COMPONENT_MANIFEST" 2>/dev/null) ||
    fail "$COMPONENT_MANIFEST is not readable inside $CONTAINER - is the bind mount there?"

printf '%s\n' "$manifest" | grep -q '"domain"[[:space:]]*:[[:space:]]*"ckw"' ||
    fail "$COMPONENT_MANIFEST inside $CONTAINER is not the ckw component"

version=$(printf '%s\n' "$manifest" |
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
[[ -n $version ]] || fail "$COMPONENT_MANIFEST has no version"

# The manifest and const.py are two places for the same number; a bump that
# touches only one of them is a bug this line catches here, not at release.
const_version=$(sed -n 's/^VERSION: Final = "\([^"]*\)"$/\1/p' \
    "$REPO_ROOT/custom_components/ckw/const.py" | head -n 1)
if [[ -n $const_version && $const_version != "$version" ]]; then
    fail "version drift: manifest.json says $version, const.py says $const_version"
fi

# --- 3: no traceback from the component -------------------------------------
# A traceback is the block that starts at "Traceback (most recent call last):"
# and ends at the first line that is not indented (the exception line) or at a
# blank line. Only blocks that mention the component fail the gate; a traceback
# from some other integration is not ours to fix here.
read -r -d '' traceback_scan <<'AWK' || true
function flush() {
    if (block ~ /custom_components[\/.]ckw/) {
        printf "%s\n", block
        found = 1
    }
    block = ""
}
/^Traceback \(most recent call last\):/ {
    if (block != "") flush()
    in_block = 1
    block = $0
    next
}
in_block {
    if ($0 ~ /^[[:space:]]*$/) {
        flush()
        in_block = 0
        next
    }
    block = block "\n" $0
    if ($0 !~ /^[[:space:]]/) {
        flush()
        in_block = 0
    }
}
END {
    if (block != "") flush()
    exit (found ? 1 : 0)
}
AWK

logs=$(podman logs "$CONTAINER" 2>&1) || fail "podman logs $CONTAINER failed"

if ! traceback=$(printf '%s\n' "$logs" | awk "$traceback_scan"); then
    fail "the container log has a Traceback mentioning custom_components.ckw:"$'\n'"$traceback"
fi

info "$HA_URL/manifest.json: 200"
info "$COMPONENT_MANIFEST: mounted inside $CONTAINER"
info "container log: no Traceback for custom_components.ckw"
info "ckw domain version: $version"
info "dev instance is healthy"
