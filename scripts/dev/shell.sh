#!/usr/bin/env bash
#
# shell.sh - open a shell inside the running dev container (#36).
#
#   mise run dev-shell
#
# Inside, /config is the repository's config/ directory and
# /config/custom_components/ckw is the working tree. `exit` returns to the host.

set -euo pipefail

readonly CONTAINER="ha-dev"

exec podman exec --interactive --tty "$CONTAINER" bash
