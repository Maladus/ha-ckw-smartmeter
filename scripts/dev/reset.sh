#!/usr/bin/env bash
#
# reset.sh - start the dev instance on a clean recorder database (#43).
#
#   mise run dev-reset              # container restart, recorder DB deleted
#   mise run dev-reset -- --full    # also wipe config/.storage (login gone)
#
# Deleting home-assistant_v2.db* is what makes statistics and backfill runs
# comparable: Home Assistant re-imports the 730-day backfill into an empty
# database instead of merging with old statistic rows (#85, #86).
#
# `.storage/` is kept by default, so the onboarding owner and the login survive;
# `--full` wipes it, which also invalidates .dev/token. Both paths end with
# `mise run dev-up` and wait for the instance to be ready again.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

fail() {
    printf 'dev-reset: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'dev-reset: %s\n' "$*"
}

full=false
for argument in "$@"; do
    case $argument in
        --full)
            full=true
            ;;
        --help | -h)
            printf 'usage: scripts/dev/reset.sh [--full]\n'
            exit 0
            ;;
        *)
            fail "unknown argument: $argument (only --full is supported)"
            ;;
    esac
done

bash "$SCRIPT_DIR/down.sh"

# WAL and SHM files belong to the same database; a leftover -wal replays writes
# into the freshly created file, which is exactly what this script removes.
shopt -s nullglob
database_files=("$REPO_ROOT"/config/home-assistant_v2.db*)
shopt -u nullglob

if ((${#database_files[@]} > 0)); then
    rm --force -- "${database_files[@]}"
    info "deleted ${#database_files[@]} recorder database file(s)"
else
    info "no recorder database to delete"
fi

if [[ $full == true ]]; then
    rm --recursive --force "$REPO_ROOT/config/.storage"
    rm --force "$REPO_ROOT/.dev/token"
    info "wiped config/.storage and .dev/token - onboarding starts over"
else
    info "kept config/.storage - the login and the owner user survive"
fi

bash "$SCRIPT_DIR/up.sh"
