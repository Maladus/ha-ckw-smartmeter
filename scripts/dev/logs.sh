#!/usr/bin/env bash
#
# logs.sh - follow the dev container's log (#36).
#
#   mise run dev-logs
#   mise run dev-logs -- --since 10m
#
# Nothing is filtered: Home Assistant's own logger configuration in
# config/configuration.yaml decides what appears (custom_components.ckw is at
# debug). Ctrl-C stops the follow, not the container.

set -euo pipefail

readonly CONTAINER="ha-dev"

# `exec` so the signal of Ctrl-C and the exit status of podman reach the caller
# directly; extra arguments are passed through unchanged.
exec podman logs --follow --tail 200 "$CONTAINER" "$@"
