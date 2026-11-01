#!/usr/bin/env bash
#
# setup.sh - bootstrap a fresh clone (#117).
#
#   mise run setup
#
# Five steps, each of them a no-op when it is already done, so this is safe to
# run again after a pull, after a toolchain bump, or on a machine that is
# already set up:
#
#   1. `mise install` - the pinned toolchain from mise.toml (python, uv, ruff,
#      gitleaks, pre-commit, markdownlint-cli2 and the node it needs)
#   2. `.venv` + `requirements_test.txt` - the test harness the gates use, kept
#      out of the interpreter mise provides so a bump of one never breaks the
#      other
#   3. `pre-commit install --install-hooks` - the commit, commit-msg and
#      pre-push hooks declared by `default_install_hook_types`
#   4. `config/` - the dev instance's state directory, seeded once from
#      scripts/dev/configuration.yaml
#   5. `scripts/dev/.env` - copied once from `.env.example`, gitignored
#
# It touches nothing outside the repository (mise's own install dir aside) and
# never starts the dev container: that stays `mise run dev-up`.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

readonly VENV="$REPO_ROOT/.venv"
readonly REQUIREMENTS="$REPO_ROOT/requirements_test.txt"
readonly CONFIG_DIR="$REPO_ROOT/config"
readonly DEV_ENV="$REPO_ROOT/scripts/dev/.env"

fail() {
    printf 'setup: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'setup: %s\n' "$*"
}

cd "$REPO_ROOT"

command -v mise >/dev/null 2>&1 ||
    fail "mise is not on PATH - install it from https://mise.jdx.dev, then run: mise run setup"
[[ -f $REQUIREMENTS ]] || fail "requirements_test.txt is missing - is this a full checkout?"

# --- 1: toolchain ---------------------------------------------------------
info "installing the pinned toolchain (mise install)"
mise install

# --- 2: the test harness --------------------------------------------------
# The interpreter is pinned by mise; a venv that already exists is reused as it
# is, so a second run only syncs requirements_test.txt.
if [[ -x $VENV/bin/python ]]; then
    info "keeping the existing .venv ($("$VENV/bin/python" --version 2>&1))"
else
    info "creating .venv"
    uv venv --python 3.13 "$VENV"
fi

info "installing requirements_test.txt into .venv"
uv pip install --quiet --python "$VENV/bin/python" --requirement "$REQUIREMENTS"

# --- 3: the git hooks -----------------------------------------------------
# default_install_hook_types in .pre-commit-config.yaml names pre-commit,
# commit-msg and pre-push, so this one call installs all three - including the
# pre-push gate (#142). --install-hooks builds the hook environments now rather
# than on the first commit.
info "installing the git hooks (pre-commit, commit-msg, pre-push)"
pre-commit install --install-hooks

# --- 4: the dev instance's state directory --------------------------------
mkdir -p "$CONFIG_DIR/custom_components/ckw"
if [[ -f $CONFIG_DIR/configuration.yaml ]]; then
    info "keeping existing config/configuration.yaml"
else
    cp "$REPO_ROOT/scripts/dev/configuration.yaml" "$CONFIG_DIR/configuration.yaml"
    info "seeded config/configuration.yaml from scripts/dev/configuration.yaml"
fi

# --- 5: the local dev settings --------------------------------------------
if [[ -f $DEV_ENV ]]; then
    info "keeping existing scripts/dev/.env"
else
    cp "$REPO_ROOT/scripts/dev/.env.example" "$DEV_ENV"
    info "created scripts/dev/.env from .env.example (gitignored; edit HA_VERSION and the CKW_* overrides there)"
fi

# --- report ---------------------------------------------------------------
info "toolchain:"
mise ls --current 2>/dev/null | grep -E '^(python|uv|ruff|gitleaks|pipx:pre-commit|node|npm:markdownlint-cli2) ' | sed 's/^/setup:   /' || true

info "next: mise run check          # gates G-1, G-2, G-3 and the docs check"
info "      mise run dev-up         # local Home Assistant instance (rootless Podman)"
info "      mise run dev-onboard    # owner user and .dev/token, once the instance is up"
