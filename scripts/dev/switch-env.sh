#!/usr/bin/env bash
#
# switch-env.sh - point the dev instance at the mock or at production CKW.
#
#   mise run dev-env-mock                     # mock, then dev-down + dev-up
#   mise run dev-env-prod                     # production, then dev-down + dev-up
#   bash scripts/dev/switch-env.sh --check    # print, write nothing
#
# The switch is one marked block in scripts/dev/.env (gitignored): the three
# CKW_* overrides that `dev-up` passes into the container. `mock` writes the
# block, `prod` removes it, so the production endpoints from
# custom_components/ckw/const.py apply again. Everything else in .env -
# HA_VERSION above all - is left exactly as it is, and running the same mode
# twice changes nothing.
#
# The instance reads these variables at startup, so a running container keeps
# the old endpoints. Restarting is therefore the default: `--restart` is
# implicit, and `--no-restart` writes the block but refuses while `ha-dev` is up
# (stop it with `mise run dev-down` first).
#
# No CKW credential is involved in either mode: `mock` needs no CKW account at
# all, and `prod` takes the client id and the redirect URI registered at CKW
# (#1, #41) in Home Assistant's application credentials.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

readonly ENV_FILE="$SCRIPT_DIR/.env"
readonly ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
readonly CONTAINER="ha-dev"

# Mock CKW API and fake Keycloak (scripts/dev/mock/README.md). The host name is
# the host as seen from inside the Podman network; the mock binds 0.0.0.0:8787
# for exactly that reason.
readonly MOCK_ORIGIN="http://host.containers.internal:8787"
readonly MOCK_API_BASE_URL="$MOCK_ORIGIN/api/v1"
readonly MOCK_AUTHORIZE_URL="$MOCK_ORIGIN/realms/CKW-prod/protocol/openid-connect/auth"
readonly MOCK_TOKEN_URL="$MOCK_ORIGIN/realms/CKW-prod/protocol/openid-connect/token"

# Production endpoints. They are the defaults in custom_components/ckw/const.py
# and are only printed here, never written: keeping them out of .env is what
# makes `prod` the plain absence of the block. Update both places together.
readonly PROD_API_BASE_URL="https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1"
readonly PROD_REALM_URL="https://id.ckw.ch/realms/CKW-prod"
readonly PROD_AUTHORIZE_URL="$PROD_REALM_URL/protocol/openid-connect/auth"
readonly PROD_TOKEN_URL="$PROD_REALM_URL/protocol/openid-connect/token"

# The block `mock` writes and `prod` deletes. Nothing but the three assignments
# lives between the markers, so they cannot be confused with anything else.
readonly BEGIN_MARKER="# >>> switch-env.sh: mock CKW endpoints >>>"
readonly END_MARKER="# <<< switch-env.sh: mock CKW endpoints <<<"
readonly MANAGED_PATTERN='^[[:space:]]*(export[[:space:]]+)?(CKW_API_BASE_URL|CKW_AUTHORIZE_URL|CKW_TOKEN_URL)[[:space:]]*='

fail() {
    printf 'dev-env: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'dev-env: %s\n' "$*"
}

usage() {
    cat <<'EOF'
usage: scripts/dev/switch-env.sh <mock|prod> [--restart|--no-restart]
       scripts/dev/switch-env.sh <check|--check>

  mock          write the mock CKW endpoints into scripts/dev/.env
  prod          remove them, so the production defaults apply
  check         print the resolved endpoints and write nothing

  --restart     dev-down, switch, dev-up (the default)
  --no-restart  only edit scripts/dev/.env; refuses while ha-dev is up
EOF
}

# True for a line that assigns one of the three overrides, with or without
# `export` and with any spacing around the name.
is_managed_line() {
    [[ $1 =~ $MANAGED_PATTERN ]]
}

container_running() {
    command -v podman >/dev/null 2>&1 || return 1
    [[ $(podman inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true) == "true" ]]
}

# Which mode scripts/dev/.env is in now, judged by the overrides it carries:
# all three mock -> mock, none -> prod, anything else -> mixed (hand-edited).
current_mode() {
    local present=0 mocked=0 line

    if [[ -f $ENV_FILE ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            is_managed_line "$line" || continue
            present=$((present + 1))
            if [[ $line == *"$MOCK_ORIGIN"* ]]; then
                mocked=$((mocked + 1))
            fi
        done < "$ENV_FILE"
    fi

    if ((present == 0)); then
        printf 'prod'
    elif ((present == 3 && mocked == 3)); then
        printf 'mock'
    else
        printf 'mixed'
    fi
}

# The effective value of one variable: what .env says, else the production
# default the integration falls back to.
resolved() {
    local name=$1 default=$2 value

    if [[ -f $ENV_FILE ]]; then
        value=$(sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?${name}[[:space:]]*=[[:space:]]*(.*)$/\2/p" \
            "$ENV_FILE" | tail -n 1)
        value=${value%"${value##*[![:space:]]}"}
        value=${value#\"}
        value=${value%\"}
        value=${value#\'}
        value=${value%\'}
        if [[ -n $value ]]; then
            printf '%s' "$value"
            return 0
        fi
    fi

    printf '%s' "$default"
}

print_mode() {
    case $1 in
        mock)
            info "mock - the local mock CKW API at $MOCK_ORIGIN"
            info "  no CKW login is needed: any client id and secret are accepted,"
            info "  the tokens are unsigned fakes, and no CKW server is contacted."
            info "  start the mock in another shell with: mise run dev-mock"
            ;;
        prod)
            info "prod - the real CKW endpoints"
            info "  a real CKW single sign-on login: the client id oe-wigger and the"
            info "  redirect URI of this instance must be registered at CKW (#1, #41)."
            ;;
        mixed)
            info "mixed - scripts/dev/.env carries some but not all of the three CKW_*"
            info "  overrides, most likely edited by hand. Run mise run dev-env-mock or"
            info "  mise run dev-env-prod to make it one of the two again."
            ;;
    esac
}

print_endpoints() {
    printf '  %-19s %s\n' "CKW_API_BASE_URL" "$(resolved CKW_API_BASE_URL "$PROD_API_BASE_URL")"
    printf '  %-19s %s\n' "CKW_AUTHORIZE_URL" "$(resolved CKW_AUTHORIZE_URL "$PROD_AUTHORIZE_URL")"
    printf '  %-19s %s\n' "CKW_TOKEN_URL" "$(resolved CKW_TOKEN_URL "$PROD_TOKEN_URL")"
}

check_mode() {
    info "scripts/dev/.env: $(if [[ -f $ENV_FILE ]]; then printf 'present'; else printf 'absent, the defaults apply'; fi)"
    print_mode "$(current_mode)"
    info "resolved endpoints:"
    print_endpoints
    info "nothing written - this is what dev-up would put in the container"
}

mock_block() {
    printf '%s\n' \
        "$BEGIN_MARKER" \
        "CKW_API_BASE_URL=$MOCK_API_BASE_URL" \
        "CKW_AUTHORIZE_URL=$MOCK_AUTHORIZE_URL" \
        "CKW_TOKEN_URL=$MOCK_TOKEN_URL" \
        "$END_MARKER"
}

# The new .env on stdout: every line kept, the managed block replaced in place
# (mock) or removed (prod), and blank lines left behind by the removal
# collapsed. mock -> prod -> mock therefore gives back the same bytes.
render_env() {
    local mode=$1 line block_line
    local in_block=false replaced=false
    local -a out=() final=()

    if [[ -f $ENV_FILE ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            if [[ $line == "$BEGIN_MARKER" ]]; then
                # An existing block is replaced where it stands, so mock -> prod
                # -> mock gives back exactly the same bytes.
                if [[ $mode == mock ]]; then
                    out+=("@@BLOCK@@")
                fi
                in_block=true
                replaced=true
                continue
            fi
            if [[ $line == "$END_MARKER" ]]; then
                in_block=false
                continue
            fi
            if [[ $in_block == true ]]; then
                continue
            fi

            if is_managed_line "$line"; then
                # The first managed line is where the block goes back in.
                if [[ $replaced == false ]]; then
                    replaced=true
                    if [[ $mode == mock ]]; then
                        out+=("@@BLOCK@@")
                    fi
                fi
                continue
            fi

            out+=("$line")
        done < "$ENV_FILE"

        if [[ $in_block == true ]]; then
            fail "the managed block in scripts/dev/.env has no closing '$END_MARKER' line - fix or delete it"
        fi
    fi

    # No override was there to replace (a fresh .env, or prod twice): append.
    # No blank separator, so the block lands where the markers put it and a
    # round trip stays byte-identical.
    if [[ $mode == mock && $replaced == false ]]; then
        out+=("@@BLOCK@@")
    fi

    for line in "${out[@]}"; do
        if [[ $line == "@@BLOCK@@" ]]; then
            while IFS= read -r block_line; do
                final+=("$block_line")
            done < <(mock_block)
            continue
        fi
        if [[ -z $line ]]; then
            # No leading blank line and no doubled one: what the removed block
            # leaves behind must not turn into a gap.
            if ((${#final[@]} == 0)) || [[ ${final[-1]} == "" ]]; then
                continue
            fi
        fi
        final+=("$line")
    done

    while ((${#final[@]} > 0)) && [[ ${final[-1]} == "" ]]; do
        unset 'final[-1]'
    done

    if ((${#final[@]} > 0)); then
        printf '%s\n' "${final[@]}"
    fi
}

switch_env() {
    local mode=$1 restart=$2 content

    if [[ ! -f $ENV_FILE ]]; then
        [[ -f $ENV_EXAMPLE ]] ||
            fail "neither scripts/dev/.env nor scripts/dev/.env.example exists - is this a full checkout?"
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        info "created scripts/dev/.env from .env.example (gitignored)"
    fi

    content=$(render_env "$mode")
    if [[ $(<"$ENV_FILE") == "$content" ]]; then
        info "scripts/dev/.env already in $mode mode - nothing to change"
    else
        # Written next to the target and moved into place: an interrupted switch
        # must not leave a truncated .env that dev-up would then source.
        local temporary="$ENV_FILE.switch-env.$$"
        if [[ -n $content ]]; then
            printf '%s\n' "$content" > "$temporary"
        else
            : > "$temporary"
        fi
        mv --force -- "$temporary" "$ENV_FILE"
        case $mode in
            mock) info "wrote the mock CKW endpoints into scripts/dev/.env" ;;
            prod) info "removed the CKW_* overrides from scripts/dev/.env - the production defaults apply" ;;
        esac
    fi

    if [[ $restart == true ]]; then
        if container_running; then
            bash "$SCRIPT_DIR/down.sh"
        fi
        bash "$SCRIPT_DIR/up.sh"
    fi
}

main() {
    local mode=${1:-} restart=true
    if [[ -z $mode ]]; then
        usage >&2
        exit 2
    fi
    shift

    case $mode in
        check | --check)
            check_mode
            return 0
            ;;
        mock | prod) ;;
        -h | --help)
            usage
            return 0
            ;;
        *)
            printf 'dev-env: unknown mode: %s\n' "$mode" >&2
            usage >&2
            exit 2
            ;;
    esac

    local argument
    for argument in "$@"; do
        case $argument in
            --restart) restart=true ;;
            --no-restart) restart=false ;;
            -h | --help)
                usage
                return 0
                ;;
            *)
                printf 'dev-env: unknown argument: %s\n' "$argument" >&2
                usage >&2
                exit 2
                ;;
        esac
    done

    # The container reads these variables once, at startup; asking for no
    # restart while it runs would leave it on the old endpoints without saying
    # so. Refuse before .env is touched.
    if [[ $restart == false ]] && container_running; then
        fail "container $CONTAINER is running and read its endpoints at startup - run 'mise run dev-down' first, or drop --no-restart to restart it here"
    fi

    switch_env "$mode" "$restart"

    print_mode "$mode"
    print_endpoints
    info "reconfigure or re-add the integration in Home Assistant to use these endpoints"
}

main "$@"
