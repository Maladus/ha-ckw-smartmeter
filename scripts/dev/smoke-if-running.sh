#!/usr/bin/env bash
#
# smoke-if-running.sh - gate G-6 for callers that may not have an instance.
#
#   mise run check          # through scripts/check.sh
#
# `mise run dev-smoke` fails on purpose when there is no container: an absent
# instance is a broken promise for the command that asks for a smoke test. For
# `mise run check` and the pre-push hook an absent container is normal (CI has
# no Podman at all), so this wrapper probes first and skips *with the reason
# printed* - the gate stays green, but nobody can mistake the skip for a pass.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

readonly CONTAINER="ha-dev"

if ! command -v podman >/dev/null 2>&1; then
    printf 'check: podman is not installed - skipping gate G-6 (mise run dev-smoke)\n'
    exit 0
fi

if [[ $(podman inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true) != "true" ]]; then
    printf 'check: dev container %s is not running - skipping gate G-6 (start it with: mise run dev-up)\n' \
        "$CONTAINER"
    exit 0
fi

cd "$REPO_ROOT"
exec mise run dev-smoke
