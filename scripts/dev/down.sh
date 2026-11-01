#!/usr/bin/env bash
#
# down.sh - stop and remove the dev container (#36).
#
#   mise run dev-down
#
# config/ is deliberately kept: it holds the onboarding state, the recorder
# database and everything else the instance learned. Only `mise run dev-reset`
# (or `--full`) touches it.

set -euo pipefail

readonly CONTAINER="ha-dev"

info() {
    printf 'dev-down: %s\n' "$*"
}

if podman container exists "$CONTAINER"; then
    podman rm --force "$CONTAINER" >/dev/null
    info "removed container $CONTAINER (config/ kept)"
else
    info "no container $CONTAINER - nothing to do"
fi
