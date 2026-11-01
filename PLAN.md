# ckw — HACS integration for CKW smart meters · Implementation Roadmap

Repository: https://github.com/Maladus/ha-ckw-smartmeter (`custom_components/ckw`)

Home Assistant custom integration (`custom_components/ckw`) that pulls daily electricity
consumption and cost from the **CKW OpenEnergy API** into HA sensors and long-term
statistics (Energy dashboard).

- API: `https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1/…`
- Auth: CKW Single Sign-On (Keycloak) `https://id.ckw.ch/realms/CKW-prod`, public client `oe-wigger`
- Data: daily, available until yesterday, per meter, HT/NT quantities in kWh + amounts in CHF
- Target: HACS/Marketplace installable, HA quality scale **Bronze** at v0.1.0, **Silver** at v0.3.0

## Project goal

**Goal:** give CKW customers their smart-meter consumption and cost inside Home Assistant —
daily kWh and CHF in the Energy dashboard, without scraping the portal and without storing
the CKW password.

**In scope**

- Official CKW OpenEnergy API (public client `oe-wigger`): daily aggregates, HT/NT split, cost in CHF
- One device per delivery point, long-term statistics, ~2-year backfill
- HACS/Marketplace installable, Bronze quality scale, German + English UI

**Out of scope** (tracked in Phase 9)

- Real-time power — the meter may legally be read once per day
- Invoices, product switching, relocation, portal charts
- Other Swiss grid operators (needs a provider registry, not this project)
- Dynamic tariff prices (covered by the companion integration `swiss-dynamic-tariffs`)

**Success criteria**

- A CKW customer installs from HACS and sees the Energy dashboard populated within 15 minutes
- No password stored, no SMS in normal operation, silent token refresh across restarts
- CI green on ruff, pytest, hassfest and the HACS action; all Bronze rules `done`
- A second maintainer can pick up any task from `PLAN.md` without asking questions

## Document structure (what lives where)

| File | Audience | Purpose | Single source of truth for |
|---|---|---|---|
| `README.md` | users | install, configure, entities, troubleshooting | user instructions |
| `info.md` | HACS/Marketplace | store blurb | — (mirrors README intro only) |
| `GOAL.md` | contributors, agents, reviewers | goal, scope, non-goals, success criteria | *why* the project exists |
| `ROADMAP.md` | users, contributors | curated public milestones | *when* things land (user-facing summary) |
| `PLAN.md` | maintainer, agents | full task breakdown, IDs, deps, gates (this file) | *how* work is sliced |
| GitHub issues + Project | contributors, agents | one issue per task, status, discussion | *who* is doing what, *when* |
| `CONTRIBUTING.md` | contributors | dev setup, gates, PR checklist | contribution process |
| `CODE_OF_CONDUCT.md` | community | behaviour expectations + enforcement | conduct |
| `SECURITY.md` | everyone | vulnerability reporting, token/secret handling | security policy |
| `AGENTS.md` | AI agents | context, gates, hard rules | agent contract |
| `docs/README.md` | contributors | doc index + conventions | doc layout |
| `docs/ckw-openenergy-api.md` | devs, agents | transcribed CKW API reference | API facts |
| `docs/architecture.md` | devs, reviewers | module map, data flow | design overview |
| `docs/adr/NNNN-*.md` | devs, agents | one decision per file | decisions |
| `docs/quality-scale.md` | reviewers | Bronze/Silver rule evidence | compliance status |
| `docs/ai-development.md` | agents, humans | AI playbook | AI usage |
| `scripts/dev/README.md` | devs, agents | Podman dev instance: up, smoke, fixture/real switch, teardown | local test procedure |
| `CHANGELOG.md` | users | release history (Keep a Changelog) | released changes |
| `LICENSE` | legal | MIT | licensing |

**Documentation rules**

1. `PLAN.md` tracks *what* the work is (scope, IDs, deps, gates); GitHub issues track *status*.
   One issue per task, 1:1 by ID. The plan row stays authoritative for scope, the issue for progress —
   when they disagree, fix the plan first, then the issue.
2. Every behaviour change updates README (if user-visible), `docs/ckw-openenergy-api.md` (if
   API-facing), `CHANGELOG.md`, and records non-obvious decisions as a new ADR.
3. ADRs are append-only: supersede with a new number, never rewrite an accepted one.
4. English for everything; German only for CKW domain terms (Zählpunkt, HT/NT, Netzbetreiber).
5. A doc that contradicts the code is a bug — fix in the same PR or open an issue the same day.

## Status legend

| Symbol | Meaning |
|---|---|
| `todo` | not started |
| `wip` | in progress |
| `blocked` | waiting on external party |
| `done` | merged and verified |
| Owner: **AI** | agent may implement end-to-end with gates |
| Owner: **AI+H** | agent drafts, human must verify on real instance |
| Owner: **H** | human only (external comms, production testing, releases) |

## Issue tracking on GitHub

`PLAN.md` stays the single list of tasks; GitHub is where they are worked. Mapping is 1:1 and
mechanical, so neither side can drift silently.

- **Title format:** `[B-4] Measurement client` — the ID prefix makes both directions greppable.
- **Body:** the plan row (details), the acceptance line, and a "Definition of done" checklist
  (gates G-1…G-7). Scope changes go into `PLAN.md` first, then the issue.
- **Milestones:** one per phase (`P0 External`, `P1 Setup`, `P1b Podman dev`, …) plus release
  milestones (`v0.1.0`, `v0.3.0`). Phase milestone = sequencing, release milestone = shipping.
- **Status** is label + issue state, never a bare comment: `status: blocked` requires a comment
  naming the blocker and the party to unblock it.
- **Branches:** `gh issue develop <N> --checkout`; PR body must contain `Closes #N`; merging flips
  the task to `Done` in the plan's progress board (checked by `sync-issues.sh --check`).
- **Ownership:** `owner: ai` issues may be executed end-to-end by an agent under the gates;
  `owner: human` never; `owner: both` only with a human verification step.

Bootstrap (once, `scripts/gh-setup.sh`):

```bash
gh repo create ckw-homeassistant --public --license MIT \
  --description "Home Assistant integration for CKW smart meters (OpenEnergy API)"
# labels + milestones from S-22/S-23, then:
scripts/sync-issues.sh          # create the 108 issues from PLAN.md
scripts/sync-issues.sh --check  # drift gate for CI
```

## Phase 0 — External prerequisites (blocking)

| ID | Task | Details | Done when | Deps | Owner |
|---|---|---|---|---|---|
| EXT-1 | Register redirect URIs at CKW | Reply to CKW contact: `https://my.home-assistant.io/redirect/oauth` and `https://<fqdn>/auth/external/callback`; state public client + PKCE S256 | CKW confirms both are set | — | H |
| EXT-2 | Request `offline_access` scope | Same mail; ask whether the client scope can be enabled so refresh tokens survive SSO idle timeout | answer in writing | EXT-1 | H |
| EXT-3 | Ask about device authorization grant | Optional, enables headless login without SMS | answer in writing | EXT-1 | H |
| EXT-4 | Ask about MFA in browser login | Is OTP enforced on the web login page or only in the API login flow? | answer in writing | — | H |
| EXT-5 | Verify API access with real token | Login in browser, exchange code with PKCE, `GET /api/v1/smartmeter/customer` → expect 200 (403/401 = audience/role problem → escalate to openenergy@ckw.ch) | 200 with customer + ≥1 meter | EXT-1 | H |
| EXT-6 | Capture redacted fixtures | Save customer, `…/days`, `…/months` responses; redact `deliverypointnumber`, customer ids, addresses | 3 JSON files in `tests/fixtures/` | EXT-5 | AI+H |
| EXT-7 | Confirm meter topology | Number of delivery points, `type`/`fuel` values, whether PV feed-in is a separate delivery point | documented in `docs/ckw-openenergy-api.md` | EXT-5 | H |
| EXT-8 | Confirm rate limits in practice | Read `x-ratelimit-limit` / `-remaining` / `-reset` values; note window length | documented | EXT-5 | AI+H |

## Phase 1 — Project setup, repository hygiene & governance

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| S-1 | Init repo | `git init`, `.gitignore` (`.storage/`, `secrets/`, `*.token`, `__pycache__`, `.venv`), `.editorconfig`, `.gitattributes` (LF) | repo public-ready | — | S | AI |
| S-2 | `manifest.json` | `domain: ckw`, `name`, `codeowners`, `config_flow: true`, `integration_type: service`, `iot_class: cloud_polling`, `quality_scale: bronze`, `dependencies: ["application_credentials"]`, `requirements: []`, `version: 0.0.1`, `documentation`, `issue_tracker` | hassfest passes | S-1 | S | AI |
| S-3 | `hacs.json` | `name`, `country: "CH"`, `render_readme: true`, `homeassistant` min version, optional `zip_release` + `filename` | HACS action passes | S-1 | S | AI |
| S-4 | Brand assets | `custom_components/ckw/brand/icon.png` (256²), `logo.png`; neutral CKW-ish colours (no trademark misuse) | files present, HACS action green | S-1 | S | AI |
| S-5 | `const.py` skeleton | `DOMAIN`, `API_BASE_URL`, `AUTHORIZE_URL`, `TOKEN_URL`, `SCOPES`, `AGGREGATIONS`, `DEFAULT_POLL_HOURS=2`, `BACKFILL_DAYS=730`, `REFRESH_SKEW=60`, `PLATFORMS` | imports cleanly, ruff clean | S-2 | S | AI |
| S-6 | `__init__.py` stub | `async_setup_entry`/`async_unload_entry` no-ops + `PLATFORMS = [Platform.SENSOR]` | HA dev instance loads entry without errors | S-2 | S | AI |
| S-7 | `strings.json` + translations | `config`, `config.error`, `config.abort`, `entity.sensor.*`, `exceptions` keys; `translations/de.json`, `en.json` | hassfest translations check green | S-2 | S | AI |
| S-8 | Test bootstrap | `pyproject.toml` (ruff, pytest config, `pytest-homeassistant-custom-component` optional extra), `tests/conftest.py` | `pytest -q` runs (0 tests ok) | S-1 | S | AI |
| S-9 | pre-commit | hooks: `check-added-large-files`, `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `ruff-check --fix`, `ruff-format` | `pre-commit run --all-files` green | S-1 | S | AI |
| S-10 | CI workflows | `.github/workflows/tests.yaml`: jobs `pre-commit`, `pytest`, `hassfest` (`home-assistant/actions/hassfest`), `hacs` (`hacs/action` category integration) | all jobs green on GitHub | S-5..S-9 | S | AI |
| S-11 | Dev instance plan | Dev HA is specified in **Phase 1b** (Podman); this task only reserves the directory `scripts/dev/` | `scripts/dev/` exists or is created by DEV-1 | S-6 | S | AI |
| S-12 | `AGENTS.md` + `CONTRIBUTING.md` | agent contract (commands, gates, hard rules) and human PR checklist | both written, gates referenced by exact command | S-9 | S | AI |
| S-13 | Issue/PR templates | `.github/ISSUE_TEMPLATE/{bug,feature}.yml`, `PULL_REQUEST_TEMPLATE.md` with quality-scale checklist | templates visible on GitHub | S-1 | S | AI |
| S-14 | `LICENSE` | Full MIT text, copyright "Martin Wigger" + year, LICENSE section in README. Rationale: HACS-typical, and permissive enough to be relicensed into Apache-2.0 for a later core submission | GitHub detects MIT, badge shown | S-1 | S | AI |
| S-15 | `GOAL.md` | Goal, in/out of scope, success criteria — the "Project goal" block of this plan, maintained separately so it survives plan rewrites | file matches this plan, linked from README | S-1 | S | AI |
| S-16 | `ROADMAP.md` | Public milestones `v0.1.0` → `v0.3.0` → `v1.0.0`, 3–6 bullets each, link to `PLAN.md` | no task IDs duplicated, roadmap matches phase order | S-15 | S | AI |
| S-17 | `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1, enforcement contact, scope (issues, PRs, discussions) | linked from README + CONTRIBUTING | S-1 | S | AI |
| S-18 | `SECURITY.md` | Supported versions, private reporting (GitHub security advisories + mail), explicit scope: token handling, reauth flow, never paste tokens/codes into issues; what is redacted in diagnostics | GitHub shows Security policy, linked from README | S-1 | S | AI |
| S-19 | `docs/README.md` | Doc index mirroring the "Document structure" table + the five documentation rules | every doc listed, no orphan docs | S-1 | S | AI |
| S-20 | Repo settings & meta | Description, topics (`home-assistant`, `hacs`, `ckw`, `switzerland`, `energy`, `smart-meter`), `.github/CODEOWNERS`, branch protection on `main` with required CI checks, `SUPPORT.md` (where to ask vs. report). Labels/milestones are S-22/S-23 | CI required on `main`, topics set | S-10 | S | H |
| S-21 | Create GitHub repo | `gh repo create ckw-homeassistant --public --description "…" --license MIT`, verify `gh auth status`, push `main`, enable Dependabot security alerts | repo exists, CI runs on first push | S-1 | S | H |
| S-22 | Label taxonomy | `type: feat\|fix\|docs\|chore\|test\|infra`, `phase: 0…9`, `status: backlog\|ready\|blocked\|review`, `owner: ai\|human\|both`, `size: s\|m\|l`, `area: auth\|api\|stats\|entities\|ci\|docs` — created by `gh label create` loop in `scripts/gh-setup.sh` | 5 label families present, naming documented | S-21 | S | AI |
| S-23 | Milestones per phase | `P0 External` … `P9 Optional` + release milestones `v0.1.0`, `v0.3.0` | `gh milestone list` shows all | S-21 | S | AI |
| S-24 | Plan → issues sync | `scripts/sync-issues.sh`: parse the task tables in `PLAN.md`, create missing issues `[ID] title` with details + acceptance + DoD checklist, assign milestone/labels; `--check` fails on drift; `--board` regenerates the progress board | one issue per task, `--check` green | S-22, S-23 | L | AI |
| S-25 | Project board | `gh project create`, fields `Phase`, `Status`, `Owner`, `Size`; views: board by Status, table by Phase, filtered "blocked" | all issues visible in the board, fields populated | S-24 | M | AI |
| S-26 | Issue/PR workflow conventions | Branch per issue (`gh issue develop N --checkout`), PR body `Closes #N`, merge flips the plan board column; written into `CONTRIBUTING.md` + `AGENTS.md` | first PR merged through the flow | S-24, S-12 | S | AI |
| S-27 | Automation guardrails | Scheduled workflow `plan-check.yml` running `scripts/sync-issues.sh --check`; optional `actions/add-to-project` for auto-adding new issues | drift detected in CI on a seeded mismatch | S-24 | M | AI |

## Phase 1b — Local test instance in Podman

Rootless Podman instead of Docker: no daemon, systemd-native autostart (quadlet), same image.
Two run modes, both on the same instance:

| Mode | Purpose | CKW login needed |
|---|---|---|
| **fixture mode** (`CKW_FIXTURE_MODE=1`) | UI, sensor and statistics iteration; offline; CI-safe | no |
| **real mode** | OAuth2 login against `oe-wigger`, polling, backfill validation | yes |

Reference unit (quadlet), `scripts/dev/ha.container`:

```ini
[Unit]
Description=Home Assistant dev (ckw)

[Container]
Image=ghcr.io/home-assistant/home-assistant:2025.8.0
ContainerName=ha-dev
PublishPort=8123:8123
Volume=%h/work/private/ckw-homeassistant/config:/config:Z
Volume=%h/work/private/ckw-homeassistant/custom_components/ckw:/config/custom_components/ckw:Z
Environment=TZ=Europe/Zurich
Environment=CKW_FIXTURE_MODE=1
EnvironmentFile=%h/work/private/ckw-homeassistant/scripts/dev/.env

[Install]
WantedBy=default.target
```

Fallback for non-systemd machines: `scripts/dev/compose.yaml` (`podman-compose`), same mounts/ports.

**Known friction to plan for**

- SELinux (Fedora/openSUSE): every bind mount needs `:Z`, otherwise HA starts with an empty config.
- Rootless ports below 1024 need `net.ipv4.ip_unprivileged_port_start`; keep HA on 8123/8443.
- The OAuth callback of a dev instance is a **different URL** than production → must be registered at
  CKW as well (EXT-1), and must be HTTPS for the browser flow → Caddy sidecar with a local CA.
- Statistics testing is only meaningful on a clean recorder DB (`home-assistant_v2.db`) — hence the reset script.

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| DEV-1 | Dev directory & compose fallback | `scripts/dev/{compose.yaml,.env.example,README.md}`, gitignored `config/` with a `configuration.yaml` template (logger, `recorder:` block), `.gitignore` entries | `podman-compose -f scripts/dev/compose.yaml up -d` serves HA on :8123 | S-6 | M | AI |
| DEV-2 | Quadlet unit + autostart | `scripts/dev/ha.container` installed to `~/.config/containers/systemd/ha-dev.container`, `%h` specifiers, pinned image, both volume mounts with `:Z` | `systemctl --user daemon-reload && systemctl --user start ha-dev` → HA reachable, `ckw` listed | DEV-1 | M | AI |
| DEV-3 | Image pinning & upgrade path | `HA_VERSION` in `.env.example`, bump procedure in README, smoke run after each bump | pinned tag documented, bump checklist exists | DEV-1 | S | AI |
| DEV-4 | Fixture mode | `CKW_FIXTURE_MODE` read in `const.py`; `api.py` serves `tests/fixtures/*.json` instead of HTTP; log a loud warning when active; never active unless the env var is set | fixture instance shows configured values, `podman logs` shows zero outbound CKW calls | B-4 | M | AI |
| DEV-5 | Onboarding script | `scripts/dev/onboard.sh`: wait for onboarding API, create owner user, optionally core config, then write a long-lived token to `.dev/token` (gitignored) | fresh volume → usable HA + token in under 3 minutes | DEV-2 | M | AI |
| DEV-6 | HTTPS callback for real login | Caddy sidecar (or documented tunnel) exposing `https://ha-dev.local:8443`, mkcert CA steps, redirect URI `…/auth/external/callback` registered at CKW | real OAuth2 login succeeds on the dev instance | EXT-1, DEV-2 | L | AI+H |
| DEV-7 | Smoke script | `scripts/dev/smoke.sh`: poll `/manifest.json`, assert `ckw` in `components`, scan `podman logs` for `ERROR`/`Traceback`, call `homeassistant.update_entity` on the CKW sensors with the token, exit non-zero on any failure | exits 0 healthy / non-zero broken; used as gate G-6 | DEV-4, DEV-5 | M | AI |
| DEV-8 | Statistics reset harness | `scripts/dev/reset-db.sh`: stop unit, delete `config/home-assistant_v2.db*`, start, wait ready | three consecutive reset→backfill runs end with identical sums (tests F-2/F-3) | DEV-2, F-2 | S | AI |
| DEV-9 | Podman runbook | `scripts/dev/README.md`: SELinux `:Z`, rootless ports, `--userns=keep-id`, `podman logs -f ha-dev`, `podman exec -it ha-dev bash`, teardown, volume cleanup | a fresh machine follows it without improvising | DEV-2 | S | AI |
| DEV-10 | Runbook wired into docs | "Test locally" section in `AGENTS.md` and `CONTRIBUTING.md` (up → smoke → fixture/real switch → reset → teardown); referenced from gate G-6 | commands copy-pasteable, linked from both files | DEV-7, S-12 | S | AI |

**Phase acceptance:** from a clean checkout, `scripts/dev/smoke.sh` green in fixture mode within 5 minutes;
real-mode login verified on the dev instance; backfill idempotency reproducible via `reset-db.sh`.

## Phase 2 — Authentication

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| A-1 | `application_credentials.py` | `async_get_authorization_server` → `AuthorizationServer(authorize_url, token_url)`; `async_get_auth_implementation` → `LocalOAuth2ImplementationWithPkce` (public client, no secret) | unit test returns both objects | S-2 | M | AI |
| A-2 | Config flow: OAuth2 step | `async_step_user` → `async_oauth_create_entry`, `VERSION/MINOR_VERSION`, entry title from customer name | flow reaches HA login, entry created | A-1, EXT-1 | M | AI+H |
| A-3 | `test-before-configure` | After OAuth, probe `GET /smartmeter/customer`; map 401/403 to `invalid_auth`, network to `cannot_connect` | flow aborts with correct error, test covers both | A-2 | S | AI |
| A-4 | `unique-config-entry` | Abort if an entry with the same customer id exists (`async_set_unique_id`) | second setup aborts `already_configured` | A-3 | S | AI |
| A-5 | Meter selection step | If >1 `deliverypointnumber`, `async_step_meter` picker; store in entry data | multi-meter fixture → picker shown; single meter → skipped | A-3, EXT-6 | M | AI |
| A-6 | Reauth flow | `async_step_reauth` → same OAuth2 flow, update tokens, `async_update_reload_and_abort` | simulated 401 on refresh → reauth offered, flow reloads entry | A-2 | M | AI |
| A-7 | Reconfigure flow | Change client id / meter without deleting the entry | reconfigure reloads entry, statistics ids unchanged | A-5 | S | AI |
| A-8 | Options flow | Poll interval (1–12 h, default 2 h) | options persist, coordinator picks up on reload | A-5 | S | AI |
| A-9 | Config-flow tests | `pytest-homeassistant-custom-component`: happy path, invalid auth, cannot connect, already configured, multi-meter, reauth | `test_config_flow.py` ≥ 6 cases green | A-3..A-8 | M | AI |
| A-10 | End-to-end login | Real browser login in `scripts/ha-dev` HA against `oe-wigger`; HA restart → no prompt | entry `loaded`, entities present, restart silent | A-2, EXT-5 | — | H |
| A-11 | Fallback: pasted-code flow | Only if CKW refuses callback registration: PKCE generate → show authorize URL → user pastes redirect URL → exchange | hidden menu entry `user_paste` works | A-11 trigger | M | AI |
| A-12 | Fallback: password + SMS OTP flow | Only if browser callback is unusable in practice: 2 steps (`/otp/login` → token with `code`), tokens only, no password stored, single-flight lock | login completes, no password in entry data | A-12 trigger | L | AI+H |

## Phase 3 — API client

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| B-1 | `api.py` skeleton | `aiohttp` via `async_get_clientsession`, base URL, timeouts, `CkwApiError`/`CkwAuthError`/`CkwRateLimitError` | ruff clean, no network in import | S-5 | S | AI |
| B-2 | `CkwCustomer`/`CkwMeter` dataclasses | Parse `customer → locations → meters`, flatten meters with location context | fixture parses, all fields typed | EXT-6 | S | AI |
| B-3 | `async_get_customer()` | GET `/v1/smartmeter/customer` | returns dataclass from fixture | B-1, B-2 | S | AI |
| B-4 | `async_get_measurements(from, to, aggregation)` | Path params `datum_von_YYYYMMDD`/`datum_bis_YYYYMMDD`/`aggregation`; parse array of `{deliverypointnumber, measurements[…]}` | fixture parses into `dict[meter, list[CkwMeasurement]]` | B-3 | M | AI |
| B-5 | Measurement model | `zeitstempel_von_utc`, `zeitstempel_bis_utc`, `menge_fakturiert_ht/_nt`, `betrag_ht/_nt`, `anzahl_linien_p/_fw/_fb`; derived `total_kwh`, `total_cost`, `quality_flag` | unit tests for derived values incl. `None` handling | B-4 | S | AI |
| B-6 | Error mapping | 400/408/401/403/429/500/503 → typed exceptions; parse `x-ratelimit-remaining`, `-reset` (ms) into `RateLimitInfo` | test per status code | B-1 | M | AI |
| B-7 | Range chunking | Split a range into ≤ N-day chunks (respect API limits, `BACKFILL_DAYS`), yield `(from, to)` pairs, Europe/Zurich safe | property test: chunks cover range without overlap/gap | B-4 | M | AI |
| B-8 | Aggregation helpers | Map user-facing interval to API enum (`day(s)`, `month(s)`, `week(s)`), document `minute(s)` as unsupported by product decision | unit test | B-4 | S | AI |
| B-9 | Diagnostics redaction | `mask_deliverypoint()` (last 4 chars), token stripping helper | used in `diagnostics.py` + debug logging | B-2 | S | AI |

## Phase 4 — Setup, coordinator, runtime data

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| C-1 | `runtime_data` | `@dataclass CkwRuntimeData(api, coordinator, meters, totals)` on `ConfigEntry.runtime_data` | typed, ruff clean | B-3 | S | AI |
| C-2 | Coordinator | `DataUpdateCoordinator[CkwData]`, `update_interval` from options (default 2 h), `_async_update_data` fetches customer+meters+window | first refresh populates data | C-1 | M | AI |
| C-3 | Rolling window | Refetch last 7 days (`days`) on every poll; merge into per-meter cumulative totals kept in `runtime_data`; ignore duplicate intervals | test: repeated poll does not change totals | C-2 | M | AI |
| C-4 | Exception mapping | `CkwAuthError`→`ConfigEntryAuthFailed`; `CkwRateLimitError`→`UpdateFailed` with reset time; network→`ConfigEntryNotReady` at setup / `UpdateFailed` at runtime | tests per case | B-6 | M | AI |
| C-5 | Setup/unload | `async_setup_entry` creates API+coordinator, `EntryType.SERVICE` device, forwards platforms; `async_unload_entry` unloads platforms and clears listeners | unload/reload test green (Silver `config-entry-unloading`) | C-1 | M | AI |
| C-6 | `log-when-unavailable` | First failure logs warning once, recovery logs info | log assertions in test | C-4 | S | AI |
| C-7 | Timezone discipline | All date math in `Europe/Zurich`; API dates are `YYYYMMDD`, timestamps UTC `dateTimeOnly` | DST test (23 h / 25 h days) | C-3 | M | AI |

## Phase 5 — Entities

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| E-1 | `entity.py` base | `CkwEntity(CoordinatorEntity)`, `has_entity_name = True`, `DeviceInfo` per meter (name, model, `identifiers`, `via_device` = service device) | device registry shows service + meter devices | C-2 | M | AI |
| E-2 | Sensor descriptions | `SensorEntityDescription` tuple with `key`, `translation_key`, `device_class`, `state_class`, `native_unit`, `suggested_display_precision`, `value_fn` | descriptions covered by strings.json | E-1 | S | AI |
| E-3 | `consumption_total` | kWh, `TOTAL_INCREASING`, cumulative since import start, `last_reset` handling on meter change | value matches fixture sums | E-2 | M | AI |
| E-4 | `consumption_yesterday` | kWh of last complete day; either attribute of E-3 or own sensor with `state_class: total` + `last_reset` | decision recorded in ADR-0003 | E-3 | S | AI |
| E-5 | `cost_total` | CHF, `TOTAL` + `last_reset`, `device_class: MONETARY`, documented excl. VAT | value = Σ betrag_ht+nt | E-2 | M | AI |
| E-6 | HT/NT detail | Separate `consumption_high_tariff` / `consumption_low_tariff` cumulative sensors (opt-in via options, default off) | toggle works | E-3 | M | AI |
| E-7 | `last_update` | `TIMESTAMP`, `entity_category: DIAGNOSTIC` | shows last successful poll | E-2 | S | AI |
| E-8 | Unique-id scheme | `f"{entry_id}_{deliverypoint}_{key}"`, append-only; documented rule "never rename, never reuse" | test asserts stable ids | E-1 | S | AI |
| E-9 | Availability | `available` false when coordinator fails; entities restore on success | matches Silver `entity-unavailable` | C-4 | S | AI |
| E-10 | Sensor tests | State + attributes from fixtures, multi-meter fan-out, missing interval (None) handling | `test_sensor.py` ≥ 5 cases | E-3..E-7 | M | AI |

## Phase 6 — Long-term statistics

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| F-1 | `statistics.py` core | `StatisticMetaData(has_mean=False, has_sum=True, source="recorder", statistic_id=<entity_id>, unit)` + `async_import_statistics` | imports without recorder errors | E-3 | M | AI |
| F-2 | Idempotent import | Recompute cumulative `sum` for the imported window; overwrite instead of append | 3× restart test → identical last sum | F-1 | M | AI |
| F-3 | Initial backfill | 730 days via `B-7` chunking at first setup; progress logging; abort-safe | Energy dashboard shows 2 years | F-2, B-7 | L | AI+H |
| F-4 | Cost statistics | Same for CHF sensor | dashboard cost graph populated | F-2 | S | AI |
| F-5 | DST correctness | Interval start = local midnight; 23/25 h days produce one statistic bucket per local day | unit tests | C-7 | M | AI |
| F-6 | Change detection | Skip import if window checksum unchanged (avoids recorder churn) | log shows skip on unchanged poll | F-2 | S | AI |
| F-7 | Validation against portal | Compare 3 random days against my.ckw.ch chart (manual cross-check) | numbers match ±0.1 kWh | F-3, EXT-5 | — | H |

## Phase 7 — Documentation

| ID | Task | Details | Done when | Deps | Owner |
|---|---|---|---|---|---|
| D-1 | `docs/ckw-openenergy-api.md` | Transcribed API reference: 3 endpoints, all fields, aggregations, rate-limit headers, auth, "data until yesterday", error codes, CKW support contact | every field traceable to the Anypoint spec | EXT-5 | AI |
| D-2 | `docs/architecture.md` | Module map, data flow (OAuth → coordinator → totals → sensors/statistics), meter↔device mapping, why sensors + import | explains each module in ≤ 1 paragraph | C-1 | AI |
| D-3 | ADRs | `0001-auth-oauth2-vs-pasted-code.md`, `0002-statistics-strategy.md`, `0003-meter-device-mapping.md`, `0004-poll-interval-and-data-lag.md` | 4 files, each with context/decision/consequences | D-2 | AI |
| D-4 | `README.md` | High-level description, prerequisites (CKW client + redirect registration), step-by-step install (HACS + manual), config fields table, entities table, data lag (1 day), removal instructions, troubleshooting (401/403/429), consent/Art. 8a–8d StromVV note, debug log instructions | a stranger installs it without asking | A-9, E-3 | AI+H |
| D-5 | `info.md` | Short store blurb for HACS/Marketplace | renders in HACS pane | D-4 | AI |
| D-6 | Translation review | Native German pass over `translations/de.json` + `strings.json` | no machine-German left | S-7 | H |
| D-7 | `CHANGELOG.md` + releases | Keep-a-changelog format, `v0.1.0` release notes, optional release-drafter workflow | release published | milestones | AI |
| D-8 | `docs/ai-development.md` | AI playbook: context files to read first, gates, hard rules, task prompt templates, review checklist, secret hygiene | agent can complete a small issue using it alone | S-12 | AI |
| D-9 | Debug section | How to raise log level, what is redacted, how to dump diagnostics | documented | B-9 | AI |
| D-10 | Documentation-maintenance rule | PR-template checkbox + CONTRIBUTING line: behaviour change ⇒ README / `docs/` / CHANGELOG updated; `docs/README.md` index kept current; stale doc = bug | checkbox present, checked during Q-2 audit | S-19 | AI |

## Phase 8 — Quality scale, verification, release

| ID | Task | Details | Done when | Deps | Owner |
|---|---|---|---|---|---|
| Q-1 | `quality_scale.yaml` | All 20 Bronze rules with `done`/`exempt` + reason | hassfest quality-scale check green | S-2 | AI |
| Q-2 | Bronze audit | Walk every Bronze rule (action-setup, appropriate-polling, brands, common-modules, config-flow, config-flow-test-coverage, dependency-transparency, docs-*, entity-event-setup, entity-unique-id, has-entity-name, runtime-data, test-before-configure, test-before-setup, unique-config-entry) and record evidence | table in `docs/quality-scale.md` | Q-1 | AI+H |
| Q-3 | Silver: `entity-unavailable` + `log-when-unavailable` | verified by pulling network in dev instance | observed behaviour matches | E-9, C-6 | H |
| Q-4 | Silver: `config-entry-unloading` | reload from UI works | verified | C-5 | AI+H |
| Q-5 | Silver: `diagnostics` | `diagnostics.py` downloadable, redacted | dump inspected manually | B-9 | AI |
| Q-6 | Silver: docs parameters | installation + options parameters documented | matches config flow | D-4 | AI |
| Q-7 | Fresh-instance smoke test | Second HA instance (or clean docker volume): HACS custom repo install → setup → dashboard | works end to end | all above | H |
| Q-8 | Marketplace submission | PR to `hacs/default` (integration list), expect months of review | PR opened | Q-7 | H |
| Q-9 | Release | `git tag v0.1.0`, notes generated, `hacs.json` version alignment | HACS shows the release | Q-7 | H |

## Phase 9 — Later / optional

| ID | Task | Trigger | Owner |
|---|---|---|---|
| OPT-1 | Password + SMS-OTP auth fallback (A-12) | HA instance unreachable from browser, or CKW withdraws callback support | AI+H |
| OPT-2 | Pasted-code auth fallback (A-11) | CKW refuses callback registration | AI |
| OPT-3 | Production / PV feed-in meter support | EXT-7 shows separate delivery point with production `type` | AI+H |
| OPT-4 | Dynamic tariff entities | User wants prices in the same integration (today: companion `swiss-dynamic-tariffs`, public CKW endpoint) | AI |
| OPT-5 | Device authorization grant login | CKW enables RFC 8628 for `oe-wigger` | AI |
| OPT-6 | HA core submission | After 3+ months of stable HACS usage: docs page in `home-assistant.io`, brands repo PR, full test harness, code review | H |

## Cross-cutting gates (every PR)

| Gate | Command |
|---|---|
| G-1 | `ruff check . && ruff format --check .` |
| G-2 | `pytest -q` |
| G-3 | `pre-commit run --all-files` |
| G-4 | hassfest (CI job or local docker `home-assistant/actions/hassfest`) |
| G-5 | `hacs/action` (CI) |
| G-6 | Dev instance: `scripts/dev/smoke.sh` green (fixture mode), `podman logs` free of tracebacks |
| G-7 | PR ≤ ~400 changed lines; behaviour change ⇒ test added |

## Hard rules (humans and agents)

1. No API field in code without a fixture or a citation in `docs/ckw-openenergy-api.md`.
2. Never persist the CKW password, an SMS code, or a refresh token in logs, fixtures, or commits.
3. Redact `deliverypointnumber`, customer id, address, tokens in fixtures, diagnostics and debug output.
4. Entity unique IDs are append-only; renaming requires an ADR.
5. New dependency ⇒ ADR + `requirements` entry + `dependency-transparency` check.
6. `manifest.json`, `strings.json`, `quality_scale.yaml` changes are always verified by hassfest in the same PR.
7. Manual-only zones (no unattended merge): token storage/refresh, reauth UX, statistics import, anything touching the recorder.

## Risk register

| Risk | Impact | Mitigation | Owner |
|---|---|---|---|
| CKW does not register redirect URIs | M1 blocked | fallback flows A-11/A-12 ready to build | H |
| Client missing API audience/roles → 403 | M2 blocked | EXT-5 catches it early; escalate to openenergy@ckw.ch | H |
| `offline_access` refused (refresh dies after 6 h) | daily reauth pain | 2 h poll keeps the chain alive; documented caveat | AI+H |
| API range limits unknown | backfill fails | chunking B-7 with conservative chunk size + retry/backoff | AI |
| Data quality flags (`anzahl_linien_*` > 1) | wrong sums | B-5 exposes flags, coordinator logs them, statistics skip flagged intervals | AI |
| Meter exchange mid-history | broken cumulative sums | detect `supply_start_date` change → new statistic id + ADR | AI+H |
| HACS → Marketplace rename (2026.11) | docs drift | repo structure unaffected; mention both names in README | AI |
| Plan and issues drift apart | unclear status, duplicated work | `sync-issues.sh --check` in CI (S-27), plan is authoritative | AI |
| Issue noise (115 issues at once) | overwhelming for contributors | milestones + `status: ready` filter; only current phase is `ready`, everything else `status: backlog` | AI |
| SELinux blocks bind mounts | dev HA boots empty, hours lost | always `:Z`, documented in DEV-9, checked by DEV-7 | AI |
| Dev instance cannot be an OAuth redirect | real login untestable locally | register the dev callback too (EXT-1) or use a tunnel; fixture mode keeps UI work unblocked | AI+H |

## Progress board

| Phase | Tasks | Done |
|---|---|---|
| 0 External | 8 | 0 |
| 1 Setup & governance | 27 | 0 |
| 1b Podman dev instance | 10 | 0 |
| 2 Auth | 12 | 0 |
| 3 API | 9 | 0 |
| 4 Coordinator | 7 | 0 |
| 5 Entities | 10 | 0 |
| 6 Statistics | 7 | 0 |
| 7 Docs | 10 | 0 |
| 8 Quality/Release | 9 | 0 |
| 9 Optional | 6 | 0 |
| **Total** | **115** | **0** |

## Critical path

`EXT-1 → A-2 → A-10 → B-4/C-2/E-3 → F-3 → Q-1 → Q-7 → Q-9`

Everything else can run in parallel; S-1..S-20 and D-1..D-3 are safe to do before CKW replies.

Must-have path to v0.1.0: **109 tasks** (all phases except Phase 9).
