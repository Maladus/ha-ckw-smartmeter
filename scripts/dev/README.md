# Local development instance (Podman)

This page answers: how do I run Home Assistant with this integration and the mock CKW API on my own
machine, and how do I get it healthy, inspected, reset and gone again?

Everything here is a mise task; the scripts under `scripts/dev/` are the only place that calls
`podman` directly (#36, #44). The instance is disposable: it lives in this checkout's `config/`
directory and in one container called `ha-dev`, nothing outside the repository is touched.

## What runs where

| Piece | Where | Reached from the host | Reached from the container |
|---|---|---|---|
| Home Assistant, pinned by `HA_VERSION` (2026.10.4) | container `ha-dev` | `http://localhost:8123` | `http://localhost:8123` |
| Integration under development | `custom_components/ckw`, bind-mounted | the working tree | `/config/custom_components/ckw` |
| Instance state (onboarding, recorder DB) | `config/`, gitignored | `config/` | `/config` |
| Mock CKW API + fake Keycloak (`mise run dev-mock`) | your shell, port 8787 | `http://localhost:8787` | `http://host.containers.internal:8787` |

## Prerequisites

- Rootless Podman 4.9 or newer, no daemon and no `sudo`. `podman info --format
  '{{.Host.Security.Rootless}}'` must print `true`. Rootful Podman is not supported here: the bind
  mounts would end up root-owned and `mise run dev-reset` could not delete the recorder database.
- A working subordinate UID/GID range. Check with `grep "^$USER:" /etc/subuid /etc/subgid`; if either
  line is missing, add it with `usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"`
  (that one needs root, once, outside this repository) and run `podman system migrate`.
- Unprivileged user namespaces enabled, which is the distribution default on Fedora, openSUSE and
  Ubuntu 24.04. On older kernels `podman info` reports the failure instead of starting anything.
- Port 8123 free on the host. The port is above 1024 on purpose, so
  `net.ipv4.ip_unprivileged_port_start` needs no change (a rootless container cannot bind privileged
  ports without it).
- `curl` on the host for the readiness wait, and the toolchain from `mise install` (Python 3.13 for
  the onboarding script).
- The image, once: `podman pull ghcr.io/home-assistant/home-assistant:2026.10.4`. It is
  `ghcr.io/home-assistant/home-assistant:<HA_VERSION>`, and `HA_VERSION` is a concrete tag in
  `scripts/dev/.env` (copied from `.env.example` by `mise run setup`), never `stable`.

## Commands

| Task | Does |
|---|---|
| `mise run dev-up` | create the mount points, seed `config/configuration.yaml` if absent, start or restart `ha-dev`, wait for `/manifest.json` (max 180 s) |
| `mise run dev-onboard` | create the owner user and finish onboarding over the API, write `.dev/token`; no-op once done |
| `mise run dev-smoke` | gate G-6: manifest 200, component visible in the container, no `Traceback` for `custom_components.ckw`, prints the effective domain version |
| `mise run dev-logs` | `podman logs -f --tail 200 ha-dev`, unfiltered; extra arguments go through (`mise run dev-logs -- --since 10m`) |
| `mise run dev-shell` | `bash` inside the container |
| `mise run dev-reset` | remove the container, delete `config/home-assistant_v2.db*`, start again |
| `mise run dev-reset -- --full` | the same, and also wipe `config/.storage` and `.dev/token` (onboarding survives unless you ask for this) |
| `mise run dev-down` | stop and remove the container, keep `config/` |
| `mise run dev-mock` | the mock CKW API and fake Keycloak on port 8787, in the foreground |
| `mise run dev-env-mock` | point the instance at the mock and restart it |
| `mise run dev-env-prod` | point the instance at production CKW and restart it |

Every task is idempotent: `dev-up` twice is a restart (`--replace`), `dev-down` twice says there is
nothing to do, `dev-onboard` twice exits immediately, and `dev-env-mock` twice changes nothing.

## First run

```bash
mise install
mise run dev-up          # ~30-60 s on the first start, prints http://localhost:8123
mise run dev-onboard     # owner user, core config, .dev/token
mise run dev-smoke       # exits 0 when the instance is healthy
```

`dev-up` seeds `config/configuration.yaml` from `scripts/dev/configuration.yaml` **only when the file
does not exist**, so edits in `config/` survive a restart. That seed enables `default_config`, debug
logging for `custom_components.ckw` only, and `recorder: purge_keep_days: 30`.

The onboarding script uses the local dev credentials `ckwdev` / `ckw-dev-password`; override them with
`DEV_USERNAME`, `DEV_PASSWORD`, `DEV_USER_NAME` and `DEV_LANGUAGE` if you prefer your own. They are
local to this container, which is why printing them is acceptable. The access token lands in
`.dev/token` (mode 0600, gitignored) for scripted API calls; it is invalid as soon as onboarding is
redone.

## Mounts, SELinux and `host.containers.internal`

Two bind mounts, both with the `:Z` suffix:

```bash
podman run -d --name ha-dev --replace \
  -p 8123:8123 \
  -v "$PWD/config:/config:Z" \
  -v "$PWD/custom_components/ckw:/config/custom_components/ckw:Z" \
  -e TZ=Europe/Zurich \
  --add-host=host.containers.internal:host-gateway \
  ghcr.io/home-assistant/home-assistant:2026.10.4
```

- `:Z` relabels the mount for SELinux (Fedora, openSUSE). Without it the container is not allowed to
  read the component: Home Assistant starts, the integration is simply missing, and nothing in the UI
  says why. On hosts without SELinux the flag is accepted and ignored, so it is always set.
- The component is mounted read-through, not copied. Editing Python under `custom_components/ckw` and
  restarting the container is enough; a manifest change or a new dependency needs the restart too.
- `host.containers.internal` is the host as seen from inside the Podman network. The mock binds
  `0.0.0.0:8787` for exactly that reason (`scripts/dev/mock/README.md`); if you start it with
  `--host 127.0.0.1` the container will not reach it and the URL above stops working.
- `config/` is owned by your user because rootless Podman maps the container's root to your UID. Files
  the container creates there are yours to delete, which is what `dev-reset` relies on.

## Switching between mock and production

The integration reads the `CKW_*` environment variables from `scripts/dev/.env` (gitignored, created
from `.env.example` on `mise run setup`); `dev-up` passes any that are set into the container. Without
them the production endpoints from `custom_components/ckw/const.py` are used.

```bash
mise run dev-env-mock   # the local mock, then dev-down + dev-up
mise run dev-env-prod   # production CKW, then dev-down + dev-up
```

Both are one command and idempotent, and neither touches anything in `.env` except the three
`CKW_*` overrides - `HA_VERSION` and your own entries stay exactly as they are:

- `dev-env-mock` writes the three overrides into a marked block in `scripts/dev/.env`. No CKW account
  is needed at all: start `mise run dev-mock` in another shell, then add the integration with any
  client id and secret, because the mock accepts anything and its tokens are unsigned fakes.
- `dev-env-prod` removes that block, so the production endpoints apply again. This is the real CKW
  single sign-on, which needs the client id `oe-wigger` entered in **Settings -> Devices & services ->
  Application credentials** and the redirect URI of this instance registered at CKW (#1); the dev
  instance has no HTTPS callback yet, so a browser login here is expected to fail until #41.

Print the endpoints that would be used without writing or restarting anything:

```bash
bash scripts/dev/switch-env.sh --check
```

| Setting | Mock (`mise run dev-env-mock`) | Production (`mise run dev-env-prod`) |
|---|---|---|
| API base URL | `http://host.containers.internal:8787/api/v1` | `https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1` |
| Authorization URL | `http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/auth` | `https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/auth` |
| Token URL | `http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/token` | `https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token` |
| Client id / secret | anything, the mock never checks them | the client id `oe-wigger` (#1) |
| CKW account | not needed | needed, with SMS second factor |

The same values are in `scripts/dev/.env.example`, whose block is already in mock mode. Restarting is
the default because the container reads these variables once, at startup;
`bash scripts/dev/switch-env.sh mock --no-restart` writes the block and refuses while `ha-dev` is up.
The mock's tokens are JWT-shaped but unsigned and nothing verifies them - never point a real client
at the mock and call it authenticated.

## Tests that need a clean database

Statistics import and the 730-day backfill only behave deterministically on an empty recorder
database (#43, #85, #86):

```bash
mise run dev-reset             # container + home-assistant_v2.db* gone, onboarding kept
mise run dev-reset -- --full   # additionally config/.storage and .dev/token
```

`--full` is the "as if freshly cloned" button: the next `mise run dev-onboard` runs the full
onboarding again.

## Bumping the dev image

`HA_VERSION` in the gitignored `scripts/dev/.env` is the whole pin (#38): the container runs
`ghcr.io/home-assistant/home-assistant:<HA_VERSION>`, and `.env.example` carries the concrete tag the
instance was last verified on. `stable` is deliberately not the default — it is a moving tag, so a
`dev-up` would upgrade Home Assistant without a commit, and a breakage would appear with nothing in the
diff to blame. (`up.sh` still falls back to `stable` when `scripts/dev/.env` is missing; a fresh clone
gets the file from `mise run setup`, so the pin is what runs.)

1. Edit `HA_VERSION` in `scripts/dev/.env` to the new release tag, e.g. `2026.10.4`.
2. `mise run dev-up` — recreates `ha-dev` from the new image and waits for the frontend.
3. `mise run dev-smoke` — gate G-6: manifest, mounted component and no traceback from
   `custom_components.ckw`. This is the step a `stable` tag would have skipped silently.
4. If Home Assistant moved to a new release line, bump the harness pin in `requirements_test.txt`
   (`pytest-homeassistant-custom-component`, which fixes the Home Assistant version the tests run
   against) and run `mise run setup` once so `.venv` follows. The image and the harness should name the
   same Home Assistant; today the harness still pins HA 2026.2.3, so the image is one sweep ahead.
5. `mise run check` — lint, docs, tests, hooks in one run, then the dev-smoke from step 3 again when a
   test or a hook changed.

The running version is what Home Assistant writes to `/config/.HA_VERSION` inside the container (the
`config/` directory of the checkout that owns `ha-dev`, so the file is on the host too); `mise run
dev-shell` opens a shell in the container if you want to read it there. If the integration breaks on the
new tag, the previous tag is one edit away — but Home Assistant does not migrate the recorder database
backwards, so copy `config/home-assistant_v2.db` first when the history matters, or `mise run dev-reset`
if it does not.

## Teardown

```bash
mise run dev-down          # container gone, config/ kept
rm -rf config .dev/token   # the instance state is gone too
podman image rm ghcr.io/home-assistant/home-assistant:2026.10.4   # optional, ~2.5 GB
```

Nothing outside the repository is created: no systemd unit, no quadlet, no registry. The optional
autostart unit is #37 and is not installed by any task here.

## Known limit: no HTTPS

The mock serves plain HTTP on localhost. That is fine for scripted calls, fixtures and API work, but
it is not a faithful OAuth round trip: CKW requires an HTTPS redirect URI, and the dev instance's
callback URL is a different one than production's. Testing the real browser login on this instance
needs the Caddy sidecar with a local CA from #41 (`https://ha-dev.local:8443`). Until then, treat
"login fails" on the dev instance as expected and verify the flow with tests instead.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `dev-up` times out, log says the config directory is empty | a bind mount without `:Z` on an SELinux host |
| `dev-up` fails with "address already in use" | something else owns port 8123; `ss -ltnp \| grep 8123` |
| the container exits immediately | `mise run dev-logs` shows why; a broken `config/configuration.yaml` is the usual reason |
| the integration is missing in the UI but `dev-smoke` passes | it is not configured yet - add it through the UI, `dev-smoke` only proves the mount and the instance |
| the container cannot reach the mock | the mock was started with `--host 127.0.0.1`, or the wrong port |
| `podman` says "cannot clone: Operation not permitted" | subordinate UID/GID range or unprivileged user namespaces are missing (see Prerequisites) |
| `dev-onboard` says onboarding is partly done | `mise run dev-reset -- --full`, then `mise run dev-onboard` |

## Related

- `scripts/dev/mock/README.md` documents the mock API, its endpoints and the error injection.
- `PLAN.md`, "Phase 1b - Local test instance in Podman" is the task list this runbook implements.
- `custom_components/ckw/const.py` holds every endpoint and tunable the integration uses.
- `docs/reference/ckw-openenergy-api-verification.md` is the measured API behaviour the mock follows.
