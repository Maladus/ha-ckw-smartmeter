#!/usr/bin/env bash
#
# up.sh - start (or restart) the local Home Assistant dev instance (#36).
#
#   mise run dev-up
#
# Creates the two mount points, seeds config/configuration.yaml once from
# scripts/dev/configuration.yaml, runs the Home Assistant image in rootless
# Podman as container `ha-dev`, and waits until the frontend answers.
#
# The instance owns config/ (gitignored, kept across restarts); the component is
# mounted straight from custom_components/ckw, so a container restart picks up
# Python changes. `--replace` makes a second `mise run dev-up` a restart instead
# of a "name already in use" error.
#
# Rootless Podman only - no sudo, no system-wide daemon. Prerequisites and the
# SELinux story are in scripts/dev/README.md (#44).

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

readonly CONTAINER="ha-dev"
readonly HA_URL="http://localhost:8123"
readonly READY_TIMEOUT_SECONDS=180
readonly IMAGE_REPOSITORY="ghcr.io/home-assistant/home-assistant"

fail() {
    printf 'dev-up: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'dev-up: %s\n' "$*"
}

command -v podman >/dev/null 2>&1 ||
    fail "podman is not on PATH - install rootless podman >= 4.9 (scripts/dev/README.md)"

# Optional local overrides: HA_VERSION for the image tag (#38), and the CKW_*
# endpoints for reference. .env is gitignored; .env.example is the template.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
    set +a
    info "using scripts/dev/.env"
fi

readonly HA_VERSION="${HA_VERSION:-stable}"
readonly IMAGE="$IMAGE_REPOSITORY:$HA_VERSION"

[[ -d "$REPO_ROOT/custom_components/ckw" ]] ||
    fail "custom_components/ckw is missing - run this from a full checkout"

# Mount points, created before Podman sees them so they are owned by this user
# rather than by the container's root mapping.
mkdir -p "$REPO_ROOT/config/custom_components/ckw"

if [[ -f "$REPO_ROOT/config/configuration.yaml" ]]; then
    info "keeping existing config/configuration.yaml"
else
    cp "$SCRIPT_DIR/configuration.yaml" "$REPO_ROOT/config/configuration.yaml"
    info "seeded config/configuration.yaml from scripts/dev/configuration.yaml"
fi

# `:Z` relabels each bind mount for SELinux (Fedora, openSUSE): without it the
# container may not read the component at all and Home Assistant starts with an
# empty config. On non-SELinux hosts the flag is accepted and ignored.
podman run -d \
    --name "$CONTAINER" \
    --replace \
    -p 8123:8123 \
    -v "$REPO_ROOT/config:/config:Z" \
    -v "$REPO_ROOT/custom_components/ckw:/config/custom_components/ckw:Z" \
    -e TZ=Europe/Zurich \
    --add-host=host.containers.internal:host-gateway \
    "$IMAGE" >/dev/null

info "started $CONTAINER from $IMAGE"
info "waiting up to ${READY_TIMEOUT_SECONDS}s for $HA_URL/manifest.json"

deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
while true; do
    if [[ $(curl -s -o /dev/null -w '%{http_code}' "$HA_URL/manifest.json" || true) == "200" ]]; then
        break
    fi

    # A container that is not running will never become ready; its last log
    # lines are the only useful thing left to print.
    if [[ $(podman inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true) != "true" ]]; then
        podman logs --tail 40 "$CONTAINER" >&2 || true
        fail "container $CONTAINER stopped before Home Assistant became ready"
    fi

    if ((SECONDS >= deadline)); then
        podman logs --tail 40 "$CONTAINER" >&2 || true
        fail "Home Assistant did not answer $HA_URL/manifest.json within ${READY_TIMEOUT_SECONDS}s"
    fi

    sleep 2
done

info "Home Assistant is up: $HA_URL"
info "onboarding: mise run dev-onboard   logs: mise run dev-logs   shell: mise run dev-shell"
info "stop: mise run dev-down            reset: mise run dev-reset"
