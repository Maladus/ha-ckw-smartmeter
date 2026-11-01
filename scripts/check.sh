#!/usr/bin/env bash
#
# check.sh - the full local gate, in one order (#117, #142).
#
#   mise run check
#
# G-1 lint -> docs -> G-2 test -> G-3 hooks, then G-6 (`mise run dev-smoke`) only
# when the dev container is actually running: a contributor without Podman, a
# CI runner and the pre-push hook all get a printed skip instead of a red gate.
#
# The pre-push hook runs exactly this, so the wall time is a budget, not a
# metric: keep it under ~90 s. Measured on the reference machine (2026-11):
# lint 0.2 s, docs 1.8 s, test 17 s, hooks 1.5 s - about 21 s without the dev
# container. The slowest step is `test`; if that ever grows past the budget,
# give it a `--full` switch and run the fast subset here, do not drop a gate.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

fail() {
    printf 'check: %s\n' "$*" >&2
    exit 1
}

step() {
    printf '\ncheck: ===== %s =====\n' "$1"
}

cd "$REPO_ROOT"

[[ -x .venv/bin/python ]] ||
    fail "no .venv - run: mise run setup"

started=$SECONDS

step "G-1 lint"
mise run lint

step "docs"
mise run docs

step "G-2 test"
mise run test

step "G-3 hooks"
mise run hooks

# G-6 is separate from G-1..G-3 because it needs the dev instance; the helper
# prints the reason it skipped, so a green gate never hides a missing check.
step "G-6 dev-smoke (skipped when the dev container is down)"
bash scripts/dev/smoke-if-running.sh

printf '\ncheck: green in %ss\n' "$((SECONDS - started))"
