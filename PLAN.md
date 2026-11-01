# ckw — HACS integration for CKW smart meters · Implementation Roadmap

Repository: <https://github.com/Maladus/ha-ckw-smartmeter> (`custom_components/ckw`) — private until #110
Issues: #1–#148, one per task row below (✅ = closed / done; #139/#140 closed as not planned)

Home Assistant custom integration (`custom_components/ckw`) that pulls daily electricity
consumption and cost from the **CKW OpenEnergy API** into HA sensors and long-term
statistics (Energy dashboard).

- API: `https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1/…`
- Auth: CKW Single Sign-On (Keycloak) `https://id.ckw.ch/realms/CKW-prod`, public client `oe-wigger`
- Data: daily, available until yesterday, per meter, HT/NT quantities in kWh + amounts in CHF
- Target: HACS/Marketplace installable, HA quality scale **Gold** — Bronze at v0.1.0, Silver at v0.3.0,
  Gold (all 21 rules, 54 rules total) at v1.0.0. **Platinum is deferred**: it grades the Python library,
  not the integration, and is only reconsidered once Gold is reached.

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

## Document structure (Diátaxis, Markdown only)

**Format: Markdown everywhere.** Home Assistant itself has no AsciiDoc pipeline — `home-assistant.io`
uses Jekyll/kramdown with `source/_integrations/<domain>.markdown`, `developers.home-assistant.io` uses
Docusaurus/MDX, HACS renders `README.md`/`info.md`. So every document here is `.md`, no front matter
(except if text is later lifted into a core docs page for #116). Linting via `markdownlint-cli2` (#127).

Documentation follows [Diátaxis](https://diataxis.fr/) — four quadrants, one document belongs to exactly one:

| Quadrant | Question it answers | Folder | Documents |
|---|---|---|---|
| **Tutorial** | "teach me, I'm new" | `docs/tutorial/` | `getting-started.md` (#119) |
| **How-to** | "how do I do X" | `docs/how-to/` | `request-ckw-client.md` (#120), `install.md` (#121), `reauthenticate.md` (#122), `backfill-history.md` (#123), `debug-connection.md` (#99), `work-with-ai-agents.md` (#98) |
| **Reference** | "what exactly is X" | `docs/reference/` | `ckw-openenergy-api.md` (#91), `entities.md` (#124), `configuration.md` (#106), `quality-scale.md` (#102) |
| **Explanation** | "why is it like this" | `docs/explanation/` | `architecture.md` (#92), `data-model.md` (#125), `decisions/` (ADRs, #93) |

`docs/README.md` (#27) is the map: four sections, "start here" per audience (user, contributor, agent),
one sentence per document, nothing else.

**Project meta (repo root, not part of the quadrants)**

| File | Audience | Purpose | Single source of truth for |
|---|---|---|---|
| `README.md` | users | pitch, prerequisites, 5-line quickstart linking the tutorial | project entry point |
| `info.md` | HACS/Marketplace | store blurb | — (mirrors README intro only) |
| `GOAL.md` | contributors, agents | goal, scope, non-goals, success criteria | *why* |
| `ROADMAP.md` | users, contributors | curated public milestones | *when* (user-facing) |
| `PLAN.md` | maintainer, agents | tasks, deps, gates (this file) | *what* |
| GitHub issues + Project | contributors, agents | one issue per task, status, discussion | *who*, *progress* |
| `CONTRIBUTING.md` | contributors | dev setup, gates, PR checklist | contribution process |
| `CODE_OF_CONDUCT.md` | community | behaviour + enforcement | conduct |
| `SECURITY.md` | everyone | vulnerability reporting, token handling | security policy |
| `AGENTS.md` | AI agents | context, gates, hard rules | agent contract |
| `CHANGELOG.md` | users | release history (Keep a Changelog) | released changes |
| `LICENSE` | legal | MIT | licensing |
| `scripts/dev/README.md` | devs, agents | Podman dev instance runbook (#44) | local test procedure |

**arc42 chapter index → Diátaxis** (for readers who think in arc42; ADRs are §9, as usual)

| arc42 | Quadrant + document | Issue |
|---|---|---|
| 1 Einführung und Ziele | meta: `GOAL.md` + `README.md` | #23, #94 |
| 2 Randbedingungen | `explanation/data-model.md` (legal once-per-day read, rate limits, VAT) | #125 |
| 3 Kontextabgrenzung | `explanation/architecture.md` (one context diagram: HA ↔ CKW IdP ↔ OpenEnergy API) | #92 |
| 4 Lösungsstrategie | `explanation/architecture.md` summary + the ADRs it points to | #92, #93 |
| 5 Bausteinsicht | `explanation/architecture.md` module table, each block linking its ADRs | #92 |
| 6 Laufzeitsicht | `explanation/architecture.md`, exactly one sequence (OAuth → coordinator → totals → sensors/statistics) | #92 |
| 7 Verteilungssicht | `scripts/dev/README.md` (Podman dev instance) + `how-to/install.md` | #44, #121 |
| 8 Querschnittliche Konzepte | `reference/conventions.md`: error mapping, redaction, Europe/Zurich discipline, unique-id rule, logging | #129 |
| 9 Architekturentscheidungen | `explanation/decisions/` — ADRs, index in `decisions/README.md` | #93 |
| 10 Qualitätsanforderungen | `quality_scale.yaml` + `reference/quality-scale.md` | #101, #102 |
| 11 Risiken und technische Schuld | risk register in this plan + the "Consequences" of each ADR | — |
| 12 Glossar | `reference/glossary.md` | #126 |

Rule: `architecture.md` (§5) links each building block to the ADRs that shaped it (§9), so the chapter
jump you use at work — block → decision → consequence — works in this repo too.

**Quality-scale documentation matrix** (source of truth: `quality_scale.yaml` #101, `mise run docs` #127)

| Rule | Tier | Document |
|---|---|---|
| `docs-high-level-description` | Bronze | `README.md` (#94) |
| `docs-installation-instructions` | Bronze | `docs/how-to/request-ckw-client.md` (#120), `docs/tutorial/getting-started.md` (#119), `docs/how-to/install.md` (#121) |
| `docs-removal-instructions` | Bronze | `docs/how-to/install.md` (#121) |
| `docs-actions`, `docs-triggers`, `docs-conditions` | Bronze | exempt — no services/triggers/conditions; reason recorded in `quality_scale.yaml` (#101) |
| `brands` | Bronze | `custom_components/ckw/brand/` (#12); brands repo PR at #116 |
| `docs-installation-parameters` | Silver | `docs/reference/configuration.md` (#106) |
| `docs-configuration-parameters` | Silver | `docs/reference/configuration.md` (#106) |
| `docs-supported-functions` | Gold | `docs/reference/entities.md` (#124) |
| `docs-supported-devices`, `docs-data-update`, `docs-known-limitations` | Gold | `docs/explanation/data-model.md` (#125) |
| `docs-troubleshooting` | Gold | `docs/how-to/debug-connection.md` (#99), `docs/how-to/reauthenticate.md` (#122) |
| `docs-use-cases` | Gold | `docs/tutorial/getting-started.md` (#119) |
| `docs-examples` | Gold | `docs/reference/examples.md` (#128) |

Note: hassfest only validates `quality_scale.yaml` **inside core**. In a HACS repo `quality_scale: bronze`
in `manifest.json` is a claim, not a checked fact — keep it only while #116 (core submission) is a goal,
and treat `quality_scale.yaml` as our own audit checklist until then.

**Documentation rules**

1. `PLAN.md` tracks *what* the work is (scope, deps, gates); the issue with the same number tracks *status*.
   The plan row stays authoritative for scope, the issue for progress — when they disagree, fix the
   plan first, then the issue.
2. Every behaviour change updates the affected quadrant document, `CHANGELOG.md`, and records
   non-obvious decisions as a new ADR.
3. ADRs are append-only: supersede with a new number, never rewrite an accepted one.
4. English for everything; German only for CKW domain terms (Zählpunkt, HT/NT, Netzbetreiber).
5. A doc that contradicts the code is a bug — fix in the same PR or open an issue the same day.
6. **One document = one quadrant.** If a page starts explaining *why* in the middle of a procedure,
   the explanation moves to `docs/explanation/` and is linked. Tutorials contain no alternatives,
   reference contains no steps, how-tos contain no theory.
7. Every document opens with one line "This page answers: …" and ends with a `Related` block linking
   its neighbours in the other quadrants — Diátaxis only works if the map is walkable.
8. `mise run docs` (#127) enforces quadrant placement, markdownlint rules and relative links in CI.

### ADR process (architecture decision records)

**Where:** `docs/adr/NNNN-kebab-case-title.md` — 4 digits, zero-padded, monotonic, never renumbered.
`0000-template.md` holds the skeleton, `docs/adr/README.md` is the index (number, title, status, date).

**Format** (MADR-mini, one page max):

```text
# NNNN — Title
- Status: proposed | accepted | deprecated | superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: <names>

## Context
## Decision
## Consequences        (positive / negative / follow-ups)
## Alternatives considered   (and why each was rejected)
## Links                (issues, code, external docs)
```

**Required when** any of these is true: new dependency · anything touching auth, tokens or secrets ·
statistics/recorder strategy · entity-id or unique-id scheme · user-visible behaviour (naming, units,
polling, backfill) · deviation from this plan or from an earlier ADR · a review round argued about it.

**Lifecycle:** `proposed` (in the PR) → `accepted` (merged, status field flipped in the same PR) →
`deprecated` or `superseded by NNNN`. Accepted ADRs are immutable except typos; a changed mind is a new
ADR that supersedes the old one. The ADR ships in the same PR as the change, or a docs-only PR before it.

**Linkage:** implementing code and docs cite `ADR-0002`; the PR body links it; the plan row is marked ✅.
**Enforcement:** PR template asks "ADR needed? linked?"; AGENTS.md makes it a hard rule — no dependency
and no auth change without an ADR.

### ADRs inside Diátaxis

Diátaxis sorts by *user need*, ADRs sort by *time*. They intersect in one place only: ADRs are
**explanation**, and they live in `docs/explanation/decisions/`.

| Concern | Where it goes | Never |
|---|---|---|
| Current behaviour/spec (what is true now) | `docs/reference/`, `docs/how-to/` | an ADR — it would rot on the next change |
| Why the current shape exists, what was rejected | ADR in `docs/explanation/decisions/` | `architecture.md` prose (history would be lost) |
| The system as it is today, in one narrative | `docs/explanation/architecture.md` | a second copy of ADR contents |
| Chronological log of decisions | `decisions/README.md` index (no, title, date, status) | `CHANGELOG.md` (that's releases, not reasoning) |
| Work to be done | the issue with the same number | an ADR |

**Linking direction:** reference/how-to point *up* to explanation when the reason matters
(`→ ADR-0002`); explanation/architecture points *out* to reference for current details; tutorials and
how-tos never cite ADRs — users don't need the archaeology. `data-model.md` (#125) links every
constraint to the ADR that derived a decision from it.

**Status is part of the explanation:** `superseded by NNNN` is itself the history, so a superseded ADR
stays in place and keeps its links. The current truth always lives in reference/how-to, never only in ADRs.

**Planned:** 0001 auth (OAuth2+PKCE vs pasted code vs password+SMS-OTP) · 0002 statistics strategy
(entity statistics via `async_import_statistics` vs external statistics) · 0003 meter↔device mapping and
unique-id scheme · 0004 poll interval and the one-day data lag.

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

`PLAN.md` and the issue tracker are the same list: **the issue number is the task identifier**.
One row = one issue, one issue = one row. The plan holds scope, dependencies and gates; the issue
holds status, assignment and discussion.

- **Numbering:** `#N` in the first column *is* the task. Titles are plain task names — no ID prefixes.
- **Bodies** carry details, "done when", dependency links and the gate checklist (G-1…G-7).
- **Milestones:** one per phase (`P0 External` … `P9 Optional`) plus release milestones (`v0.1.0`, `v0.3.0`).
- **Labels:** `type:` `phase:` `status:` `owner:` `size:` `area:`; `status: backlog` until a phase is active.
- **Status** is label + issue state, never a bare comment; `status: blocked` needs a comment naming the blocker.
- **Branches:** `gh issue develop <N> --checkout`; PR body contains `Closes #N`; merging closes the issue
  and the plan row is marked ✅ in the same PR.
- **Drift gate:** `scripts/sync-issues.sh --check` compares plan rows with open/closed issues (count, title,
  milestone) and fails CI when they disagree. The plan stays authoritative for scope.

Bootstrap status: issues #1–#116 created, milestones 1–13 created, labels created.
Issue #24 turns this one-off into the re-runnable script.

## Toolchain and tasks (mise)

Everything is pinned and run through mise. `mise install` gives the exact toolchain, and
`mise run <task>` is the **only** entry point — no bare `python`/`pip`/`podman` commands in docs,
CI or AGENTS.md. Tracked as #117.

| Tool | Pin | Why |
|---|---|---|
| `python` | 3.13 | runtime of the HA image the dev instance uses |
| `uv` | latest | venv + dependency resolution and locking (#19) |
| `ruff` | 0.17.3 | lint + format (gate G-1) |
| `gitleaks` | 8.30.1 | secret scan (gates G-3, #118) |
| `pipx:pre-commit` | 4.7.0 | hook manager (gate G-3, #17) |
| `npm:markdownlint-cli2` | latest | Markdown lint for every doc (#127) |
| `podman` | system, >= 4.9, rootless | dev HA instance; **not** mise-pinned — rootless setup is host-level (subuid/subgid, `net.ipv4.ip_unprivileged_port_start`), documented in #44 |

| Task | Runs | Purpose |
|---|---|---|
| `mise run setup` | `mise install`, `uv sync`, `pre-commit install --install-hooks`, bootstrap `config/` + `.env` | once after clone/pull |
| `mise run dev-up` | `podman run` of the HA dev container (config + component mounted, `:Z`) | start the dev instance (#36) |
| `mise run dev-down` | stop + remove the container | stop |
| `mise run dev-logs` | `podman logs -f ha-dev` | debugging |
| `mise run dev-shell` | `podman exec -it ha-dev bash` | inspect inside |
| `mise run dev-reset` | stop, delete recorder DB, start | statistics/backfill tests (#43) |
| `mise run dev-smoke` | manifest check, traceback scan, forced entity refresh | gate G-6 (#42) |
| `mise run lint` / `mise run fmt` | `ruff check` / `ruff format` | gate G-1 |
| `mise run test` | `pytest -q` | gate G-2 |
| `mise run hooks` | `pre-commit run --all-files` | gate G-3 |
| `mise run check` | lint + docs + test + hooks + dev-smoke | full local gate |
| `mise run docs` | `markdownlint-cli2` + `scripts/docs-check.sh` | doc lint, quadrant placement, link check (#127) |
| `mise run issues` | `scripts/sync-issues.sh` | plan/issue drift gate (#32) |

## Implementation order

Documentation and the goal come first — they define scope, and every later task is checked against
them. Code that lands before `GOAL.md` and the doc skeleton exists gets rewritten.

| Slice | Issues | Exit criterion |
|---|---|---|
| **0 Foundations** | #23 goal · #24 roadmap · #27 docs map · #93 ADR template + 0001–0005 · #126 glossary · #129 conventions · #91 API reference · #92 architecture · #125 data model · #94 README · #127 doc lint | `docs/` exists with all four quadrants, `GOAL.md` states scope/non-goals, `mise run docs` green — **no integration code yet** |
| **1 Contract** | #1–#4, #91, #119, #120 | CKW client verified against the real API; tutorial path written down |
| **2 Skeleton** | #10–#35, #117 | component loads in the dev instance, CI + gates green |
| **3 Auth** | #46–#48, #51–#53 | browser login works, token refresh survives restart |
| **4 Data** | #58–#66, #70–#80 | kWh + CHF entities update from the real API |
| **5 History** | #85–#90 | 2-year backfill, idempotent across restarts |
| **6 Dev instance** | #36–#45 | `mise run dev-up` + `dev-smoke` work for contributors |
| **7 Silver/Gold** | #101–#137 | all 54 quality-scale rules accounted for (done/exempt) |
| **8 Release** | #107–#110, #116 | tagged release, repo public, (optionally) core submission |

## Phase 0 — External prerequisites (blocking)

| ID | Task | Details | Done when | Deps | Owner |
|---|---|---|---|---|---|
| #1 | Register redirect URIs at CKW | Reply to CKW contact: `https://my.home-assistant.io/redirect/oauth` and `https://<fqdn>/auth/external/callback`; state public client + PKCE S256 | CKW confirms both are set | — | H |
| #2 | Request `offline_access` scope | Same mail; ask whether the client scope can be enabled so refresh tokens survive SSO idle timeout | answer in writing | #1 | H |
| #3 | Ask about device authorization grant | Optional, enables headless login without SMS | answer in writing | #1 | H |
| #4 | Ask about MFA in browser login | Is OTP enforced on the web login page or only in the API login flow? | answer in writing | — | H |
| #5 | Verify API access with real token | Login in browser, exchange code with PKCE, `GET /api/v1/smartmeter/customer` → expect 200 (403/401 = audience/role problem → escalate to <openenergy@ckw.ch>) | 200 with customer + ≥1 meter | #1 | H |
| #6 | Capture redacted fixtures | Save customer, `…/days`, `…/months` responses; redact `deliverypointnumber`, customer ids, addresses | 3 JSON files in `tests/fixtures/` | #5 | AI+H |
| #7 | Confirm meter topology | Number of delivery points, `type`/`fuel` values, whether PV feed-in is a separate delivery point | documented in `docs/ckw-openenergy-api.md` | #5 | H |
| #8 | Confirm rate limits in practice | Read `x-ratelimit-limit` / `-remaining` / `-reset` values; note window length | documented | #5 | AI+H |

## Phase 1 — Project setup, repository hygiene & governance

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| ✅ #9 | Init repo | `git init`, `.gitignore` (`.storage/`, `secrets/`, `*.token`, `__pycache__`, `.venv`), `.editorconfig`, `.gitattributes` (LF) | repo public-ready | — | S | AI |
| #10 | `manifest.json` | `domain: ckw`, `name`, `codeowners`, `config_flow: true`, `integration_type: service`, `iot_class: cloud_polling`, `quality_scale: bronze`, `dependencies: ["application_credentials"]`, `requirements: []`, `version: 0.0.1`, `documentation`, `issue_tracker` | hassfest passes | #9 | S | AI |
| #11 | `hacs.json` | `name`, `country: "CH"`, `render_readme: true`, `homeassistant` min version, optional `zip_release` + `filename` | HACS action passes | #9 | S | AI |
| #12 | Brand assets | `custom_components/ckw/brand/icon.png` (256²), `logo.png`; neutral CKW-ish colours (no trademark misuse) | files present, HACS action green | #9 | S | AI |
| #13 | `const.py` skeleton | `DOMAIN`, `API_BASE_URL`, `AUTHORIZE_URL`, `TOKEN_URL`, `SCOPES`, `AGGREGATIONS`, `DEFAULT_POLL_HOURS=2`, `BACKFILL_DAYS=730`, `REFRESH_SKEW=60`, `PLATFORMS` | imports cleanly, ruff clean | #10 | S | AI |
| #14 | `__init__.py` stub | `async_setup_entry`/`async_unload_entry` no-ops + `PLATFORMS = [Platform.SENSOR]` | HA dev instance loads entry without errors | #10 | S | AI |
| #15 | `strings.json` + translations | `config`, `config.error`, `config.abort`, `entity.sensor.*`, `exceptions` keys; `translations/de.json`, `en.json` | hassfest translations check green | #10 | S | AI |
| #16 | Test bootstrap | `pyproject.toml` (ruff, pytest config, `pytest-homeassistant-custom-component` optional extra), `tests/conftest.py` | `pytest -q` runs (0 tests ok) | #9 | S | AI |
| ✅ #17 | pre-commit (locked) | Hooks: `check-added-large-files`, `check-yaml`, `check-json`, `check-merge-conflict`, `check-case-conflict`, `detect-private-key`, `end-of-file-fixer`, `trailing-whitespace`, `mixed-line-ending`, `gitleaks --staged`, `ruff-check`, `ruff-format`, artifact block (`.dev/`, `*.token`, `config/.storage/`, `*.db`); tools pinned in `mise.toml`; installed for `pre-commit` + `pre-push` | `pre-commit run --all-files` green, a staged fake token is rejected | #9 | S | AI |
| #118 | Secret scanning in CI + push protection | `.github/workflows/secrets.yml` (`gitleaks detect --redact` on push/PR, works on a private repo with free minutes), enable GitHub secret scanning + push protection at publish (#110, free only when public), document policy in `SECURITY.md` (#25) | workflow fails on a seeded leak, policy documented | #18, #25 | S | AI |
| #117 | mise toolchain & task runner | Pin `python`, `uv`, `ruff`, `gitleaks`, `pipx:pre-commit` in `mise.toml`; expose every workflow as a mise task (`setup`, `dev-up/-down/-logs/-shell/-reset/-smoke`, `lint`, `fmt`, `test`, `hooks`, `check`, `issues`); CI and docs call the tasks only, never raw interpreters. Podman stays host-level (rootless prerequisites in #44) | fresh clone: `mise install && mise run setup && mise run check` succeeds; no doc, CI job or AGENTS.md line invokes `python`, `pip` or `podman` directly | #9 | M | AI |
| #142 | Pre-push gate: full check before any merge | Extend the `pre-push` hook to run `mise run check` (lint, docs, test, hooks) and abort on failure; worktree agents may only `/merge` when it is green; `MISE_SKIP_GATE=1` escape hatch documented for the release manager. Needed because GitHub branch protection is unavailable on a private repo without Pro, and merges happen locally | a failing test or lint error rejects the push in under 90 s | #117, #127 | S | AI |
| ✅ #18 | CI workflows | `.github/workflows/tests.yaml`: jobs `pre-commit`, `pytest`, `hassfest` (`home-assistant/actions/hassfest`), `hacs` (`hacs/action` category integration) | all jobs green on GitHub | #13–#17 | S | AI |
| #19 | Dev instance plan | Dev HA is specified in **Phase 1b** (Podman); this task only reserves the directory `scripts/dev/` | `scripts/dev/` exists or is created by #36 | #14 | S | AI |
| #20 | `AGENTS.md` + `CONTRIBUTING.md` | agent contract (commands, gates, hard rules) and human PR checklist | both written, gates referenced by exact command | #17 | S | AI |
| #21 | Issue/PR templates | `.github/ISSUE_TEMPLATE/{bug,feature}.yml`, `PULL_REQUEST_TEMPLATE.md` with quality-scale checklist | templates visible on GitHub | #9 | S | AI |
| ✅ #22 | `LICENSE` | Full MIT text, copyright "Martin Wigger" + year, LICENSE section in README. Rationale: HACS-typical, and permissive enough to be relicensed into Apache-2.0 for a later core submission | GitHub detects MIT, badge shown | #9 | S | AI |
| ✅ #23 | `GOAL.md` | Goal, in/out of scope, success criteria — the "Project goal" block of this plan, maintained separately so it survives plan rewrites | file matches this plan, linked from README | #9 | S | AI |
| ✅ #24 | `ROADMAP.md` | Public milestones `v0.1.0` → `v0.3.0` → `v1.0.0`, 3–6 bullets each, link to `PLAN.md` | no task IDs duplicated, roadmap matches phase order | #23 | S | AI |
| #25 | `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1, enforcement contact, scope (issues, PRs, discussions) | linked from README + CONTRIBUTING | #9 | S | AI |
| #26 | `SECURITY.md` | Supported versions, private reporting (GitHub security advisories + mail), explicit scope: token handling, reauth flow, never paste tokens/codes into issues; what is redacted in diagnostics | GitHub shows Security policy, linked from README | #9 | S | AI |
| ✅ #27 | `docs/README.md` | The Diátaxis map: four quadrant sections, "start here" per audience (user, contributor, agent), one sentence per document, link to every doc under `docs/`; nothing else | every doc linked, no orphan documents, `mise run docs` green | #9 | S | AI |
| #28 | Repo settings & meta | Description, topics (`home-assistant`, `hacs`, `ckw`, `switzerland`, `energy`, `smart-meter`), `.github/CODEOWNERS`, branch protection on `main` with required CI checks, `SUPPORT.md` (where to ask vs. report). Labels/milestones are #30/#31 | CI required on `main`, topics set | #18 | S | H |
| ✅ #29 | Create GitHub repo | `gh repo create ckw-homeassistant --private` (stays private until #110), verify `gh auth status`, push `main`, enable Dependabot security alerts | repo exists, CI runs on first push | #9 | S | H |
| #30 | Label taxonomy | `type: feat\|fix\|docs\|chore\|test\|infra`, `phase: 0…9`, `status: backlog\|ready\|blocked\|review`, `owner: ai\|human\|both`, `size: s\|m\|l`, `area: auth\|api\|stats\|entities\|ci\|docs` — created by `gh label create` loop in `scripts/gh-setup.sh` | 5 label families present, naming documented | #29 | S | AI |
| #31 | Milestones per phase | `P0 External` … `P9 Optional` + release milestones `v0.1.0`, `v0.3.0` | `gh milestone list` shows all | #29 | S | AI |
| #32 | Plan → issues sync | `scripts/sync-issues.sh`: parse the task tables in `PLAN.md`, create missing issues `[ID] title` with details + acceptance + DoD checklist, assign milestone/labels; `--check` fails on drift; `--board` regenerates the progress board | one issue per task, `--check` green | #30, #31 | L | AI |
| #33 | Project board | `gh project create`, fields `Phase`, `Status`, `Owner`, `Size`; views: board by Status, table by Phase, filtered "blocked" | all issues visible in the board, fields populated | #32 | M | AI |
| #34 | Issue/PR workflow conventions | Branch per issue (`gh issue develop N --checkout`), PR body `Closes #N`, merge flips the plan board column; written into `CONTRIBUTING.md` + `AGENTS.md` | first PR merged through the flow | #32, #20 | S | AI |
| #35 | Automation guardrails | Scheduled workflow `plan-check.yml` running `scripts/sync-issues.sh --check`; optional `actions/add-to-project` for auto-adding new issues | drift detected in CI on a seeded mismatch | #32 | M | AI |

## Phase 1b — Local test instance in Podman

Every task here is a **mise task** (`mise run dev-up`, `mise run dev-down`, ...) per the Toolchain
section. The shell scripts behind them live in `scripts/dev/` and are the only place that calls
`podman` directly. Quadlet is the optional autostart path, not the default.

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
  CKW as well (#1), and must be HTTPS for the browser flow → Caddy sidecar with a local CA.
- Statistics testing is only meaningful on a clean recorder DB (`home-assistant_v2.db`) — hence the reset script.

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| #36 | Dev directory & container runner | `scripts/dev/{up.sh,down.sh,reset.sh,smoke.sh,onboard.sh,configuration.yaml,.env.example,README.md}`, gitignored `config/`, `custom_components/ckw/` mount point; `mise run dev-up` starts HA rootless via plain `podman run` (no podman-compose on the target host) | `mise run dev-up` serves HA on :8123 with `config/` and the component mounted | #14, #118 | M | AI |
| #37 | Quadlet unit + autostart (optional) | `scripts/dev/ha.container` installed to `~/.config/containers/systemd/ha-dev.container`, `%h` specifiers, pinned image, both volume mounts with `:Z` | `systemctl --user daemon-reload && systemctl --user start ha-dev` -> HA reachable, `ckw` listed | #36 | M | AI |
| #38 | Image pinning & upgrade path | `HA_VERSION` in `.env.example`, bump procedure in README, smoke run after each bump | pinned tag documented, bump checklist exists | #36 | S | AI |
| #39 | Fixture mode | `CKW_FIXTURE_MODE` read in `const.py`; `api.py` serves `tests/fixtures/*.json` instead of HTTP; log a loud warning when active; never active unless the env var is set | fixture instance shows configured values, `podman logs` shows zero outbound CKW calls | #61 | M | AI |
| #40 | Onboarding script | `scripts/dev/onboard.sh`: wait for onboarding API, create owner user, optionally core config, then write a long-lived token to `.dev/token` (gitignored) | fresh volume → usable HA + token in under 3 minutes | #37 | M | AI |
| #41 | HTTPS callback for real login | Caddy sidecar (or documented tunnel) exposing `https://ha-dev.local:8443`, mkcert CA steps, redirect URI `…/auth/external/callback` registered at CKW | real OAuth2 login succeeds on the dev instance | #1, #37 | L | AI+H |
| #42 | Smoke script | `mise run dev-smoke` (G-6): `scripts/dev/smoke.sh` polls `/manifest.json`, asserts `ckw` in `components`, scans `podman logs` for `ERROR`/`Traceback`, calls `homeassistant.update_entity` on the CKW sensors with the token, exits non-zero on failure | exits 0 healthy / non-zero broken | #39, #40 | M | AI |
| #43 | Statistics reset harness | `scripts/dev/reset-db.sh`: stop unit, delete `config/home-assistant_v2.db*`, start, wait ready | three consecutive reset→backfill runs end with identical sums (tests #85/#86) | #37, #85 | S | AI |
| #44 | Podman runbook | `scripts/dev/README.md`: rootless prerequisites (subuid/subgid, `net.ipv4.ip_unprivileged_port_start`), SELinux `:Z`, `--userns=keep-id`, `mise run dev-logs`, `mise run dev-shell`, teardown, volume cleanup | a fresh machine follows it without improvising | #36 | S | AI |
| #45 | Runbook wired into docs | "Test locally" section in `AGENTS.md` and `CONTRIBUTING.md` using only mise tasks (`mise run setup`, `dev-up`, `dev-smoke`, `dev-reset`); gate G-6 references `mise run dev-smoke` | commands copy-pasteable, linked from both files | #42, #20 | S | AI |
| ✅ #141 | Local mock CKW API for offline development | `scripts/dev/mock/` (aiohttp) + fake Keycloak: three endpoints from `tests/fixtures/`, auth/token/otp/password endpoints, `x-ratelimit-*` headers, `?fail=`/`?slow=` injection, deterministic fixture generator, `mise run dev-mock` | full setup + one poll against the mock, `?fail=401` produces the reauth path | #91, #39 | M | AI |
| ✅ #144 | Mock and fixtures must match the measured API | Rework mock + fixtures against `docs/reference/ckw-openenergy-api-verification.md`: `customer.User[].locations[].meters[]`, enums `SMART/ANALOGUE`, `import/export/consumption/production`, `elec/gas`, `anzahl_linien_* = 1` normally, 400 missing-token gate, documented error bodies, `minute(s)` = 15-minute rows, `x-ratelimit-limit: 4`, unknown-key tolerance, `?fail=quota`, `?range=too-big` | fixtures and tests match the measured shapes; no test asserts contradicted behaviour | #141, #143 | M | AI |

**Phase acceptance:** from a clean checkout, `scripts/dev/smoke.sh` green in fixture mode within 5 minutes;
real-mode login verified on the dev instance; backfill idempotency reproducible via `reset-db.sh`.

## Phase 2 — Authentication

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| #46 | `application_credentials.py` | `async_get_authorization_server` → `AuthorizationServer(authorize_url, token_url)`; `async_get_auth_implementation` → `LocalOAuth2ImplementationWithPkce` (public client, no secret) | unit test returns both objects | #10 | M | AI |
| #47 | Config flow: OAuth2 step | `async_step_user` → `async_oauth_create_entry`, `VERSION/MINOR_VERSION`, entry title from customer name | flow reaches HA login, entry created | #46, #1 | M | AI+H |
| #48 | `test-before-configure` | After OAuth, probe `GET /smartmeter/customer`; map 401/403 to `invalid_auth`, network to `cannot_connect` | flow aborts with correct error, test covers both | #47 | S | AI |
| #49 | `unique-config-entry` | Abort if an entry with the same customer id exists (`async_set_unique_id`) | second setup aborts `already_configured` | #48 | S | AI |
| #50 | Meter selection step | If >1 `deliverypointnumber`, `async_step_meter` picker; store in entry data | multi-meter fixture → picker shown; single meter → skipped | #48, #6 | M | AI |
| #51 | Reauth flow | `async_step_reauth` → same OAuth2 flow, update tokens, `async_update_reload_and_abort` | simulated 401 on refresh → reauth offered, flow reloads entry | #47 | M | AI |
| ✅ #148 | Session lifetime analysis + keep-alive rule | `docs/explanation/session-lifetime.md` (lifetime model in Keycloak terms, tolerance table, probe procedure, open questions incl. the unverified Android-app observation) and `docs/explanation/decisions/0007-token-lifetime-and-keepalive.md` (2 h poll, refresh before every request, single-flight lock, persist rotated token before next use, reauth on dead refresh token, ask CKW for `offline_access`) | lifetime model with numbers, keep-alive rule implementable without further analysis | #71, #51 | M | AI |
| #52 | Reconfigure flow | Change client id / meter without deleting the entry | reconfigure reloads entry, statistics ids unchanged | #50 | S | AI |
| #53 | Options flow | Poll interval (1–12 h, default 2 h) | options persist, coordinator picks up on reload | #50 | S | AI |
| #54 | Config-flow tests | `pytest-homeassistant-custom-component`: happy path, invalid auth, cannot connect, already configured, multi-meter, reauth | `test_config_flow.py` ≥ 6 cases green | #48–#53 | M | AI |
| #55 | End-to-end login | Real browser login in `scripts/ha-dev` HA against `oe-wigger`; HA restart → no prompt | entry `loaded`, entities present, restart silent | #47, #5 | — | H |
| #56 | Fallback: pasted-code flow | Only if CKW refuses callback registration: PKCE generate → show authorize URL → user pastes redirect URL → exchange | hidden menu entry `user_paste` works | #56 trigger | M | AI |
| ~~#57~~ | Fallback: password + SMS OTP flow | Only if browser callback is unusable in practice: 2 steps (`/otp/login` → token with `code`), tokens only, no password stored, single-flight lock | login completes, no password in entry data | #57 trigger | L | AI+H — **dropped by ADR-0006** |

## Phase 3 — API client

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| #58 | `api.py` skeleton | `aiohttp` via `async_get_clientsession`, base URL, timeouts, `CkwApiError`/`CkwAuthError`/`CkwRateLimitError` | ruff clean, no network in import | #13 | S | AI |
| #59 | `CkwCustomer`/`CkwMeter` dataclasses | Parse `customer → locations → meters`, flatten meters with location context | fixture parses, all fields typed | #6 | S | AI |
| #60 | `async_get_customer()` | GET `/v1/smartmeter/customer` | returns dataclass from fixture | #58, #59 | S | AI |
| #61 | `async_get_measurements(from, to, aggregation)` | Path params `datum_von_YYYYMMDD`/`datum_bis_YYYYMMDD`/`aggregation`; parse array of `{deliverypointnumber, measurements[…]}` | fixture parses into `dict[meter, list[CkwMeasurement]]` | #60 | M | AI |
| #62 | Measurement model | `zeitstempel_von_utc`, `zeitstempel_bis_utc`, `menge_fakturiert_ht/_nt`, `betrag_ht/_nt`, `anzahl_linien_p/_fw/_fb`; derived `total_kwh`, `total_cost`, `quality_flag` | unit tests for derived values incl. `None` handling | #61 | S | AI |
| #63 | Error mapping | 400/408/401/403/429/500/503 → typed exceptions; parse `x-ratelimit-remaining`, `-reset` (ms) into `RateLimitInfo` | test per status code | #58 | M | AI |
| #64 | Range chunking | Split a range into ≤ N-day chunks (respect API limits, `BACKFILL_DAYS`), yield `(from, to)` pairs, Europe/Zurich safe | property test: chunks cover range without overlap/gap | #61 | M | AI |
| #65 | Aggregation helpers | Map user-facing interval to API enum (`day(s)`, `month(s)`, `week(s)`), document `minute(s)` as unsupported by product decision | unit test | #61 | S | AI |
| #66 | Diagnostics redaction | `mask_deliverypoint()` (last 4 chars), token stripping helper | used in `diagnostics.py` + debug logging | #59 | S | AI |
| #147 | Pace requests against the rate limit | Chunker respects `x-ratelimit-remaining`/`-reset` (observed: 4 requests per ~1 s), conservative range size verified against the "response too big" 400, resumable backfill, 429 = wait not fail | 730-day backfill completes without 429; simulated exhaustion makes the client sleep | #62, #66 | S | AI |

## Phase 4 — Setup, coordinator, runtime data

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| #67 | `runtime_data` | `@dataclass CkwRuntimeData(api, coordinator, meters, totals)` on `ConfigEntry.runtime_data` | typed, ruff clean | #60 | S | AI |
| #68 | Coordinator | `DataUpdateCoordinator[CkwData]`, `update_interval` from options (default 2 h), `_async_update_data` fetches customer+meters+window | first refresh populates data | #67 | M | AI |
| #69 | Rolling window | Refetch last 7 days (`days`) on every poll; merge into per-meter cumulative totals kept in `runtime_data`; ignore duplicate intervals | test: repeated poll does not change totals | #68 | M | AI |
| #70 | Exception mapping | `CkwAuthError`→`ConfigEntryAuthFailed`; `CkwRateLimitError`→`UpdateFailed` with reset time; network→`ConfigEntryNotReady` at setup / `UpdateFailed` at runtime | tests per case | #63 | M | AI |
| #71 | Setup/unload | `async_setup_entry` creates API+coordinator, `EntryType.SERVICE` device, forwards platforms; `async_unload_entry` unloads platforms and clears listeners | unload/reload test green (Silver `config-entry-unloading`) | #67 | M | AI |
| #72 | `log-when-unavailable` | First failure logs warning once, recovery logs info | log assertions in test | #70 | S | AI |
| #73 | Timezone discipline | All date math in `Europe/Zurich`; API dates are `YYYYMMDD`, timestamps UTC `dateTimeOnly` | DST test (23 h / 25 h days) | #69 | M | AI |

## Phase 5 — Entities

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| #74 | `entity.py` base | `CkwEntity(CoordinatorEntity)`, `has_entity_name = True`, `DeviceInfo` per meter (name, model, `identifiers`, `via_device` = service device) | device registry shows service + meter devices | #68 | M | AI |
| #75 | Sensor descriptions | `SensorEntityDescription` tuple with `key`, `translation_key`, `device_class`, `state_class`, `native_unit`, `suggested_display_precision`, `value_fn` | descriptions covered by strings.json | #74 | S | AI |
| #76 | `consumption_total` | kWh, `TOTAL_INCREASING`, cumulative since import start, `last_reset` handling on meter change | value matches fixture sums | #75 | M | AI |
| #77 | `consumption_yesterday` | kWh of last complete day; either attribute of #76 or own sensor with `state_class: total` + `last_reset` | decision recorded in ADR-0003 | #76 | S | AI |
| #78 | `cost_total` | CHF, `TOTAL` + `last_reset`, `device_class: MONETARY`, documented excl. VAT | value = Σ betrag_ht+nt | #75 | M | AI |
| #79 | HT/NT detail | Separate `consumption_high_tariff` / `consumption_low_tariff` cumulative sensors (opt-in via options, default off) | toggle works | #76 | M | AI |
| #80 | `last_update` | `TIMESTAMP`, `entity_category: DIAGNOSTIC` | shows last successful poll | #75 | S | AI |
| #81 | Unique-id scheme | `f"{entry_id}_{deliverypoint}_{key}"`, append-only; documented rule "never rename, never reuse" | test asserts stable ids | #74 | S | AI |
| #82 | Availability | `available` false when coordinator fails; entities restore on success | matches Silver `entity-unavailable` | #70 | S | AI |
| #83 | Sensor tests | State + attributes from fixtures, multi-meter fan-out, missing interval (None) handling | `test_sensor.py` ≥ 5 cases | #76–#80 | M | AI |

## Phase 6 — Long-term statistics

| ID | Task | Details | Done when | Deps | Size | Owner |
|---|---|---|---|---|---|---|
| #84 | `statistics.py` core | `StatisticMetaData(has_mean=False, has_sum=True, source="recorder", statistic_id=<entity_id>, unit)` + `async_import_statistics` | imports without recorder errors | #76 | M | AI |
| #85 | Idempotent import | Recompute cumulative `sum` for the imported window; overwrite instead of append | 3× restart test → identical last sum | #84 | M | AI |
| #86 | Initial backfill | 730 days via `#64` chunking at first setup; progress logging; abort-safe | Energy dashboard shows 2 years | #85, #64 | L | AI+H |
| #87 | Cost statistics | Same for CHF sensor | dashboard cost graph populated | #85 | S | AI |
| #88 | DST correctness | Interval start = local midnight; 23/25 h days produce one statistic bucket per local day | unit tests | #73 | M | AI |
| #89 | Change detection | Skip import if window checksum unchanged (avoids recorder churn) | log shows skip on unchanged poll | #85 | S | AI |
| #90 | Validation against portal | Compare 3 random days against my.ckw.ch chart (manual cross-check) | numbers match ±0.1 kWh | #86, #5 | — | H |

## Phase 7 — Documentation

| ID | Task | Details | Done when | Deps | Owner |
|---|---|---|---|---|---|
| ✅ #91 | `docs/reference/ckw-openenergy-api.md` | Transcribed API reference: 3 endpoints, all fields, aggregations, rate-limit headers, auth, "data until yesterday", error codes, CKW support contact. Lookup only, no procedures | every field traceable to the Anypoint spec | #1 | AI |
| ✅ #92 | `docs/explanation/architecture.md` | arc42 chapters 3/4/5/6 in one document: context diagram, building-block table (each block linking the ADRs that shaped it), one runtime sequence (OAuth → coordinator → totals → sensors/statistics); carries the "arc42 chapter index → Diátaxis" table as appendix. Omitted on purpose: formal view templates and §7 deployment (Podman runbook covers it) | explains each module in <= 1 paragraph; a newcomer reads it in 10 minutes; every block has an ADR link | #46 | AI |
| ✅ #93 | `docs/explanation/decisions/` (ADRs) | `0000-template.md` + `README.md` index + `0001-auth-oauth2-vs-pasted-code.md`, `0002-statistics-strategy.md`, `0003-meter-device-mapping.md`, `0004-poll-interval-and-data-lag.md`, `0005-doc-structure-diataxis-arc42-lite.md` (format and lifecycle per "ADR process") | template + 4 ADRs, every one with real alternatives and a status field | #92 | AI |
| ✅ #94 | `README.md` | Entry point, not a manual: pitch, prerequisites checklist, 5-line quickstart linking `docs/tutorial/getting-started.md`, feature/entity summary, data lag, consent/Art. 8a–8d StromVV note, links into all four quadrants. Drafted in slice 0 from `GOAL.md`, completed once entities exist | a stranger knows within 30 seconds whether it fits and where to start | #23, #27 | AI+H |
| #95 | `info.md` | Short store blurb for HACS/Marketplace (Markdown), mirrors the README intro | renders in HACS pane | #94 | AI |
| #96 | Translation review | Native German pass over `translations/de.json` + `strings.json` | no machine-German left | #15 | H |
| #97 | `CHANGELOG.md` + releases | Keep-a-changelog format, `v0.1.0` release notes, optional release-drafter workflow | release published | milestones | AI |
| #98 | `docs/how-to/work-with-ai-agents.md` | AI playbook: context files to read first (arc42 chapter index), gate tasks (`mise run check`), hard rules, task prompt templates, review checklist, secret hygiene; links to `reference/conventions.md` for the crosscutting rules | agent can complete a small issue using it alone | #20, #129 | AI |
| #99 | `docs/how-to/debug-connection.md` | Symptoms → cause → fix for 401/403/429/network, raising the log level, dumping diagnostics, what is redacted, where the data ends up. No theory (link to explanation) | the three most likely failures each have a concrete fix path | #66 | AI |
| #100 | Documentation-maintenance rule | PR-template checkbox + CONTRIBUTING line: behaviour change ⇒ affected quadrant doc + CHANGELOG updated; `docs/README.md` index kept current; quadrant placement + links checked by `mise run docs` (#127); stale doc = bug | checkbox present, `mise run docs` enforced in CI, checked during #102 audit | #27 | AI |
| #119 | `docs/tutorial/getting-started.md` | One linear 15-minute path: request the CKW client → install → log in → populated Energy dashboard. Prerequisites as a checklist, every step with its expected visible result, no alternatives (they live in how-to) | a first-time HACS user reaches a populated dashboard without asking | #120, #121 | AI |
| #120 | `docs/how-to/request-ckw-client.md` | The CKW OpenEnergy access form, what to enter, the mail with the redirect URIs (`https://my.home-assistant.io/redirect/oauth`, `https://<fqdn>/auth/external/callback`), the `offline_access` request, expected reply contents, what is *not* needed (public client, no secret) | request can be sent without questions; answer can be used directly | #1 | AI |
| #121 | `docs/how-to/install.md` | HACS custom repository and manual `custom_components/ckw` copy, restart, then "add integration"; note that HACS cannot install a private repo until #110; removal steps | both paths verified on a clean instance (#107) | #94 | AI |
| #122 | `docs/how-to/reauthenticate.md` | Symptoms (entity unavailable, reauth notification, `ConfigEntryAuthFailed`) → exact fix path in the UI; what *not* to do (repeated logins, revoking the client); SMS/OTP variant once #53 exists | symptom→cause→fix table, no advice that triggers repeated SMS or lockout | #48 | AI |
| #123 | `docs/how-to/backfill-history.md` | Re-import 730 days after a meter exchange or recorder wipe, delete statistics for a statistic id in Developer Tools, `mise run dev-reset` for the dev instance, what re-import overwrites (idempotent sums, #86) and what it cannot repair | history restorable without hand-editing the database; duplicate-sum pitfall called out | #87, #89 | AI |
| #124 | `docs/reference/entities.md` | Per meter: entity name, `unique_id` shape, unit, `device_class`, `state_class`, `last_reset` behaviour, statistic id, update cadence, availability semantics; which entities are Energy-dashboard eligible; append-only id rule | every created entity appears with exact attributes, generated from the entity descriptions | #76, #78 | AI |
| ✅ #125 | `docs/explanation/data-model.md` | Why data stops at yesterday (legal once-per-day read), HT/NT semantics, CHF amounts as base price without VAT/surcharges, meaning of `anzahl_linien_*` quality flags, why there is no live power sensor | reader understands the constraints without reading code or the CKW spec; every claim linked to its source. Owns the Gold doc rules `docs-supported-devices` (CKW smart meters yes, legacy/non-CKW meters no), `docs-data-update` (2 h poll, data until yesterday) and `docs-known-limitations` | #91 | AI |
| ✅ #126 | `docs/reference/glossary.md` | arc42 §12 harvested: deliverypointnumber/Zählpunkt, HT/NT, aggregation (`day(s)`, `month(s)`, …), statistic id, billed vs physical quantity, blind current, `anzahl_linien_*`, base price vs VAT | every German/API term used in code or docs has one canonical English definition | #91 | S | AI |
| ✅ #127 | markdownlint + `mise run docs` | `.markdownlint.yaml`, `markdownlint-cli2` pinned in `mise.toml`, `scripts/docs-check.sh`: every file under `docs/` sits in exactly one quadrant folder, is linked from `docs/README.md`, and has the "This page answers:" opening plus a `Related` block; relative links resolve | CI job fails on a misplaced doc, a broken relative link or a lint violation | #117, #27 | M | AI |
| ✅ #143 | Co-author trailers | `scripts/hooks/co_author.py` as a `commit-msg` hook: appends `Co-Authored-By: pi (<model>) <noreply@pi.local>` (model only, never the provider) when `PI_PROVIDER`/`PI_MODEL` are set, never for a plain shell, never twice; how-to in `docs/how-to/commits.md` | agent commit carries one trailer, human commit none | #17, #129 | S | AI |
| #128 | `docs/reference/examples.md` | Gold `docs-examples`: 3-5 copy-paste automations/blueprints — cost alert (`numerical_state` on month cost), daily consumption notification from statistics, Energy-dashboard card snippet, and an example of using HT/NT split for load shifting | every example renders and works as written; each states its entity ids | #124 | S | AI |
| ✅ #145 | Correct reference + data model against the measurements | Fix `docs/reference/ckw-openenergy-api.md` and `docs/explanation/data-model.md`: nested `User`, enums, redaction of `email`/`phone`, `anzahl_linien_*` semantics, error table with bodies, range cap, quarter-hour `minute(s)`, 4-request rate limit, dynamic-price parameters, day boundary stated as an inference | neither document contradicts the verification page; open questions stay open | #143 | M | AI |
| ✅ #146 | ADR-0006: measured auth constraints | Only browser authorization-code remains (password grant refused, `response_mode=fragment` breaks HA's callback); mark 0001 partially superseded in its Status field; drop #53, note #50 as the fallback | ADR merged, index updated, #53 closed as not planned | #93, #143 | S | AI |
| ✅ #129 | `docs/reference/conventions.md` | arc42 §8 as reference: exception mapping (`CkwAuthError` → `ConfigEntryAuthFailed`, 429 → `UpdateFailed`), redaction rules, Europe/Zurich discipline, append-only unique-id scheme, logging levels per failure class, naming (domain `ckw`, module layout) | every crosscutting rule stated once, testable, and cited from `architecture.md` and `AGENTS.md` | #92, #63 | S | AI |

## Quality-scale target: Gold

54 rules total (Bronze 20, Silver 10, Gold 21, Platinum 3). Tier is reached only when every rule of
that tier **and all lower tiers** is `done` or `exempt` with a reason. Tracking: #101 (`quality_scale.yaml`), #102 (Bronze audit), #137 (Silver + Gold audit).

| Tier | Status in plan | Rules needing new work |
|---|---|---|
| **Bronze** | covered by #10–#35, #101, #102 | — |
| **Silver** | #48 (reauth), #57 (unload), #63 (exceptions), #99 (logs), #106 (doc params) | #135 `parallel-updates`, #136 `test-coverage` |
| **Gold** | #92/#124/#125/#126/#128 (docs), #105 (diagnostics), #52 (reconfigure), #80 (device class/category) | #130 `exception-translations`, #131 `icon-translations`, #132 `entity-disabled-by-default`, #133 `repair-issues`, #134 `dynamic-devices` + `stale-devices` |
| **Platinum** | **deferred — revisit only after Gold is verified at v1.0.0** | needs a separately published client library (`async-dependency`, `inject-websession`) plus strict typing on top of all 54 rules. No user-visible benefit; cost: second repo, PyPI releases, version pinning. #139/#140 closed as not planned; reopening them is a conscious post-Gold decision, not a work item |

Expected `exempt` (with reason, checked by #137): `discovery`, `discovery-update-info` (cloud service,
nothing on the LAN to discover; identity comes from OAuth), `action-setup`, `docs-actions`,
`docs-triggers`, `docs-conditions` (no services, triggers or conditions), `dependency-transparency`
(no third-party requirement — everything runs on HA and stdlib).

## Phase 8 — Quality scale, verification, release

| ID | Task | Details | Done when | Deps | Owner |
|---|---|---|---|---|---|
| #101 | `quality_scale.yaml` | All 20 Bronze rules with `done`/`exempt` + reason | hassfest quality-scale check green | #10 | AI |
| #102 | Bronze audit | Walk every Bronze rule (action-setup, appropriate-polling, brands, common-modules, config-flow, config-flow-test-coverage, dependency-transparency, docs-*, entity-event-setup, entity-unique-id, has-entity-name, runtime-data, test-before-configure, test-before-setup, unique-config-entry) and record evidence | table in `docs/quality-scale.md` | #101 | AI+H |
| #103 | Silver: `entity-unavailable` + `log-when-unavailable` | verified by pulling network in dev instance | observed behaviour matches | #82, #72 | H |
| #104 | Silver: `config-entry-unloading` | reload from UI works | verified | #71 | AI+H |
| #105 | Silver: `diagnostics` | `diagnostics.py` downloadable, redacted | dump inspected manually | #66 | AI |
| #106 | Silver: docs parameters | installation + options parameters documented | matches config flow | #94 | AI |
| #107 | Fresh-instance smoke test | Second HA instance (or clean docker volume): HACS custom repo install → setup → dashboard | works end to end | all above | H |
| #108 | Marketplace submission | PR to `hacs/default` (integration list), expect months of review | PR opened | #107 | H |
| #109 | Release | `git tag v0.1.0`, notes generated, `hacs.json` version alignment | HACS shows the release | #107 | H |
| #110 | Publish the repo | Flip `gh repo edit --visibility public --accept-visibility-change-consequences` **only when**: README complete, no secrets in history (`git log -p \| grep -i token`), fixtures redacted, CI green, LICENSE detected. Then re-check topics + description, run `#107` as a stranger would | repo public, HACS custom-repo install works from a clean instance | #101, #94 | S | H |
| #130 | Exception translations (Gold) | Every user-facing error from `strings.json` `exceptions:`, raised as `HomeAssistantError` with `translation_domain`/`translation_key`: invalid auth, rate limit (with reset time), no meters, unsupported meter, API unavailable; nothing raw reaches the UI | every failure path translated de/en, tests assert keys not strings | #63, #15 | S | AI |
| #131 | Icon translations (Gold) | `icons.json` keyed by `translation_key`; no `_attr_icon` in Python | hassfest icons check green, zero icons in code | #76 | S | AI |
| #132 | Entity disabled by default (Gold) | Rare entities registered with `entity_registry_enabled_default=False`: HT/NT detail, quality flags, raw timestamps — instead of an options toggle | fresh setup enables exactly the 4 core entities per meter | #80 | S | AI |
| #133 | Repair issues (Gold) | `issue_registry` for user-fixable problems, deleted on recovery: meter exchange (`supply_start_date` change), persistent quality flags (`anzahl_linien_* > 1`), repeated 403 with CKW contact, missing statistics after a long outage | each issue has a translation key, severity and fix path; create/delete symmetric and tested | #87, #130 | M | AI |
| #134 | Dynamic and stale devices (Gold) | New delivery points appear at runtime without reload; vanished meters are marked stale and removable by the user (history stays attached) | second meter in the CKW portal shows up within one poll; vanished meter marked stale | #76, #70 | M | AI |
| #135 | Parallel updates (Silver) | `PARALLEL_UPDATES = 0` per platform with a one-line reason | constant set, noted in `reference/conventions.md` (#129) | #76 | S | AI |
| #136 | Test coverage >= 95% (Silver) | `--cov-fail-under=95`; fixtures for multi-meter, DST day, quality flags, missing intervals; config flow + coordinator error paths included | CI enforces the threshold, report attached to the release checklist | #19, #32 | L | AI |
| #137 | Silver + Gold audit and tier claim | All 54 rules accounted for with evidence in `reference/quality-scale.md` and `quality_scale.yaml`; expected `exempt`: `discovery`, `discovery-update-info`, `action-setup`, `docs-actions/triggers/conditions`, `dependency-transparency` | every `done` links code/docs, every `exempt` states why | #102, #130, #131, #132, #133, #134, #136 | M | AI |

## Phase 9 — Later / optional

| ID | Task | Trigger | Owner |
|---|---|---|---|
| #111 | Password + SMS-OTP auth fallback (#57) | HA instance unreachable from browser, or CKW withdraws callback support | AI+H |
| #112 | Pasted-code auth fallback (#56) | CKW refuses callback registration | AI |
| #113 | Production / PV feed-in meter support | #7 shows separate delivery point with production `type` | AI+H |
| #114 | Dynamic tariff entities | User wants prices in the same integration (today: companion `swiss-dynamic-tariffs`, public CKW endpoint) | AI |
| #115 | Device authorization grant login | CKW enables RFC 8628 for `oe-wigger` | AI |
| #138 | Clean client boundary + strict typing | `api.py` imports nothing from `homeassistant` (the coordinator maps `CkwAuthError`/`CkwRateLimitError` to HA exceptions), session + token injected as constructor arguments, full annotations, `mypy --strict` in the gates and CI; boundary recorded in `reference/conventions.md` (#129) and ADR-0006. Useful on its own — not a Platinum step | `api.py` free of HA imports, `mypy --strict` green | #61, #129 | M | AI |
| #116 | HA core submission | After 3+ months of stable HACS usage: docs page in `home-assistant.io`, brands repo PR, full test harness, code review | H |

## Cross-cutting gates (every PR)

| Gate | Command |
|---|---|
| G-1 | `mise run lint` |
| G-2 | `mise run test` |
| G-3 | `mise run hooks` |
| G-4 | hassfest (CI job or local docker `home-assistant/actions/hassfest`) |
| G-5 | `hacs/action` (CI) |
| G-6 | `mise run dev-smoke` (fixture mode), no tracebacks in `mise run dev-logs` |
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
| CKW does not register redirect URIs | M1 blocked | fallback flows #56/#57 ready to build | H |
| Client missing API audience/roles → 403 | M2 blocked | #5 catches it early; escalate to <openenergy@ckw.ch> | H |
| `offline_access` refused (refresh dies after 6 h) | daily reauth pain | 2 h poll keeps the chain alive; documented caveat | AI+H |
| API range limits unknown | backfill fails | chunking #64 with conservative chunk size + retry/backoff | AI |
| Data quality flags (`anzahl_linien_*` > 1) | wrong sums | #62 exposes flags, coordinator logs them, statistics skip flagged intervals | AI |
| Meter exchange mid-history | broken cumulative sums | detect `supply_start_date` change → new statistic id + ADR | AI+H |
| HACS → Marketplace rename (2026.11) | docs drift | repo structure unaffected; mention both names in README | AI |
| Repo still private at release | HACS cannot install private repos (no token support) | #110 gates the flip: public before #107/#108, never during active auth/session debugging | H |
| Plan and issues drift apart | unclear status, duplicated work | `sync-issues.sh --check` in CI (#35), plan is authoritative | AI |
| Issue noise (115 issues at once) | overwhelming for contributors | milestones + `status: ready` filter; only current phase is `ready`, everything else `status: backlog` | AI |
| SELinux blocks bind mounts | dev HA boots empty, hours lost | always `:Z`, documented in #44, checked by #42 | AI |
| Dev instance cannot be an OAuth redirect | real login untestable locally | register the dev callback too (#1) or use a tunnel; fixture mode keeps UI work unblocked | AI+H |

## Progress board

| Phase | Tasks | Done |
|---|---|---|
| 0 External | 8 | 0 |
| 1 Setup & governance | 30 | 8 |
| 1b Podman dev instance | 12 | 2 |
| 2 Auth | 13 | 1 |
| 3 API | 10 | 0 |
| 4 Coordinator | 7 | 0 |
| 5 Entities | 10 | 0 |
| 6 Statistics | 7 | 0 |
| 7 Docs | 24 | 13 |
| 8 Quality/Release | 18 | 0 |
| 9 Optional | 7 | 0 |
| **Total** | **145** | **23** |

## Critical path

`#23 (goal) → #27 (docs map) → #93 (ADRs) → #91/#92/#125 (reference, architecture, data model) → #1 → #46–#48 → #61 → #76 → #87 → #101 → #107 → #110`

Slices 0 and 1 need nothing from CKW and nothing from code — that is where the project starts.
Gold adds `#130–#137` on top of the Bronze/Silver path; it does not change the order, only the finish line.
