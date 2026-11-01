#!/usr/bin/env bash
#
# pre_push_gate.sh - the fast inner loop in front of the pull request checks (#142, #159).
#
#   git push        # runs this through the pre-push hook
#
# `main` is branch-protected (#110) and only a pull request with the three
# required checks merges (#159). This hook keeps the loop fast: `git push` first
# runs the same `mise run check` CI runs, and a red gate aborts the push before
# anything leaves the working copy, so a red branch never becomes a PR. The
# companion gitleaks hook scans the full history in the same stage
# (.pre-commit-config.yaml).
#
# Escape hatch, for the release manager only: MISE_SKIP_GATE=1 skips the gate
# after a loud warning. It is documented in CONTRIBUTING.md, "The pre-push gate";
# never use it to get a red branch reviewed, and never use `git push --no-verify`,
# which would also skip the secret scan.

set -euo pipefail

if [[ ${MISE_SKIP_GATE:-} == "1" ]]; then
    cat >&2 <<'WARNING'
********************************************************************************
* WARNING: MISE_SKIP_GATE=1 - the pre-push gate is DISABLED for this push.     *
* `mise run check` did NOT run: lint, docs, tests, hooks and the secret scan   *
* are unverified. Only the release manager may do this, and never to get a red *
* branch reviewed. Run `mise run check` before the push that matters; the PR    *
* checks decide the merge (#159).                                              *
********************************************************************************
WARNING
    exit 0
fi

exec mise run check
