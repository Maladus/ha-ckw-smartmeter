#!/usr/bin/env bash
#
# uninstall-timer.sh - stop and remove the ckw-next user timer and service.
#
#   bash scripts/dev/uninstall-timer.sh
#
# Idempotent: a second run reports that nothing is left to remove. Nothing else
# is touched - the repository, mise and the issue tracker are unaffected.

set -euo pipefail

readonly UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly UNITS=(ckw-next.timer ckw-next.service)

info() {
    printf 'uninstall-timer: %s\n' "$*"
}

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop ckw-next.timer 2>/dev/null || true
    systemctl --user disable ckw-next.timer 2>/dev/null || true
fi

for unit in "${UNITS[@]}"; do
    if [[ -f "$UNIT_DIR/$unit" ]]; then
        rm -f "$UNIT_DIR/$unit"
        info "removed $UNIT_DIR/$unit"
    else
        info "no $UNIT_DIR/$unit - nothing to remove"
    fi
done

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    # A failed oneshot run leaves an entry in `systemctl --user --failed`.
    systemctl --user reset-failed ckw-next.service 2>/dev/null || true
fi

info "ckw-next timer removed"
