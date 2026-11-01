#!/usr/bin/env bash
#
# install-timer.sh - install the user systemd timer that runs the next-task
# check every 30 minutes (docs/how-to/next-task.md).
#
#   bash scripts/dev/install-timer.sh
#
# Idempotent: the units are rewritten from scripts/dev/systemd/ with @REPO@
# replaced by this checkout's absolute path, then reloaded and restarted. Running
# it again after `git pull` is safe and is the supported way to pick up a change
# to the unit files.
#
# The run itself is `mise run next --notify`, so a tick that finds startable work
# pings the coordinator through `workmux send ckw-homeassistant` (or notify-send,
# or a journal line). Without systemd user units, use the crontab line in
# docs/how-to/next-task.md instead.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly UNIT_SOURCE="$SCRIPT_DIR/systemd"
readonly UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly UNITS=(ckw-next.service ckw-next.timer)

fail() {
    printf 'install-timer: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'install-timer: %s\n' "$*"
}

command -v systemctl >/dev/null 2>&1 ||
    fail "systemctl not found - no systemd here; use the crontab line in docs/how-to/next-task.md"
systemctl --user show-environment >/dev/null 2>&1 ||
    fail "no systemd user manager for $USER - use the crontab line in docs/how-to/next-task.md"
command -v mise >/dev/null 2>&1 ||
    fail "mise not found on PATH - the unit runs 'mise run next --notify'"

for unit in "${UNITS[@]}"; do
    [[ -f "$UNIT_SOURCE/$unit" ]] || fail "missing unit template $UNIT_SOURCE/$unit"
done

mkdir -p "$UNIT_DIR"

# sed's replacement text treats \\ & and the delimiter specially; a checkout
# path normally has none of them, but a path with an ampersand must not produce
# a corrupted unit.
escaped_repo=${REPO_ROOT//\\/\\\\}
escaped_repo=${escaped_repo//&/\\&}
escaped_repo=${escaped_repo//|/\\|}

for unit in "${UNITS[@]}"; do
    sed "s|@REPO@|$escaped_repo|g" "$UNIT_SOURCE/$unit" >"$UNIT_DIR/$unit"
    info "installed $UNIT_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable ckw-next.timer
# restart, not `enable --now`: a re-install has to pick up the new unit text.
systemctl --user restart ckw-next.timer
info "ckw-next.timer enabled and running every 30 minutes"
info "to run while logged out: loginctl enable-linger $USER"

systemctl --user list-timers --no-pager
