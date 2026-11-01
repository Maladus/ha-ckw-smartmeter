#!/usr/bin/env bash
#
# pre_push_gate.sh - branch protection for a repository that cannot have it (#142).
#
#   git push        # runs this through the pre-push hook
#
# GitHub refuses protected branches on a private repository without a paid plan,
# so the equivalent gate lives on this machine: `git push` first runs the same
# `mise run check` CI runs, and a red gate aborts the push before anything
# leaves the working copy. The companion gitleaks hook scans the full history in
# the same stage (.pre-commit-config.yaml).
#
# Escape hatch, for the release manager only: MISE_SKIP_GATE=1 skips the gate
# after a loud warning. It is documented in CONTRIBUTING.md, "The pre-push gate";
# never use it to get a red branch merged, and never use `git push --no-verify`,
# which would also skip the secret scan.

set -euo pipefail

if [[ ${MISE_SKIP_GATE:-} == "1" ]]; then
    cat >&2 <<'WARNING'
********************************************************************************
* WARNING: MISE_SKIP_GATE=1 - the pre-push gate is DISABLED for this push.     *
* `mise run check` did NOT run: lint, docs, tests, hooks and the secret scan   *
* are unverified. Only the release manager may do this, and never to get a red *
* branch merged. Run `mise run check` before the merge that matters.           *
********************************************************************************
WARNING
    exit 0
fi

exec mise run check
