# Changelog

All notable changes to the CKW Smart Meter integration are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Versions before `1.0.0` may change
user-visible behaviour in a minor release; every such change is listed.

## [Unreleased]

### Added

- `mise run dev-env-mock` and `mise run dev-env-prod` switch the dev instance between the local mock and
  the production CKW endpoints in one command. `scripts/dev/switch-env.sh` keeps the three `CKW_*`
  overrides in a marked block in the gitignored `scripts/dev/.env`, leaves everything else (notably
  `HA_VERSION`) alone, restarts the container so the new endpoints take effect, and prints which mode is
  active; `--check` prints the resolved endpoints without writing anything. `prod` needs the client id
  `oe-wigger` and the redirect URI registered at CKW, `mock` needs no CKW account at all.
- [Reference: fresh-install verification](docs/reference/fresh-install-check.md), the recorded acceptance run on
  a clean Home Assistant instance: the public repository cloned into an empty `config/`, onboarding over the API,
  the mock login with application credentials, the meter picker, the created entry, its entities, the 79 imported
  statistics rows, a diagnostics download without a token or a full delivery point, and the INFO lines of a 730-day
  backfill. It also records what a first install does *not* show and the one deprecation warning a user will meet (#107).
- `mise run setup` bootstraps a fresh clone in one step: the pinned toolchain, the `.venv` the gates use,
  the `pre-commit`, `commit-msg` and `pre-push` git hooks, and the gitignored `config/` and
  `scripts/dev/.env`. Every workflow is a documented mise task that runs from the repository root, and CI
  calls only those tasks, so no document, script or workflow calls a raw interpreter any more (#117).
- A `pre-push` hook that runs `mise run check` and refuses a red push, with `MISE_SKIP_GATE=1` as the
  documented escape hatch: the local stand-in for branch protection, which GitHub does not offer on a
  private repository without a paid plan (#142).
- A redacted diagnostics download on the entry's page, for a bug report: version, options, one row per
  delivery point with only its last four characters, the coordinator's health (last poll, exception
  type, rate limit), the statistics progress and two token booleans. No token, no customer number (only
  a short hash), no address and no e-mail address ever reach the file. Gold rule `diagnostics`, #105 -
  the last open rule of that tier, so `manifest.json` now claims `quality_scale: gold`.

## [0.1.0] - 2026-11-01

The first version that runs. It is exercised against the mock CKW API, the recorded fixtures and the
local Home Assistant instance; a login with a real CKW account is not verified yet, see "Known
limitations".

### Added

- Login with the CKW Single Sign-On account through the browser, as a public OAuth2 client with
  PKCE and no secret; the access token is refreshed silently and the refresh token is kept on the
  instance, never in the log.
- Setup probes the account before creating the entry and aborts with a translated reason when the
  client is unknown, has no audience, or CKW cannot be reached.
- One config entry per CKW account, with a Zählpunkt (delivery point) picker when the account has more
  than one electricity meter; gas meters are never offered.
- Reauthentication flow when CKW stops accepting the login, and a reconfigure flow to move an entry to
  another delivery point without losing its history.
- Options flow: poll interval of 1, 2, 4, 6 or 12 hours (default 2 hours) and whether setup imports the
  730-day history.
- Daily consumption sensors per delivery point: consumption total, consumption yesterday, last update
  and last published interval are enabled; high tariff, low tariff and quarter-hour consumption are
  registered but disabled by default.
- Long-term statistics per meter for the Energy dashboard: one bucket per local day in
  `Europe/Zurich`, hourly resolution, correct on the 23-hour and 25-hour DST days.
- A 730-day backfill on first setup, resumable across restarts, paced below the API's rate limit and
  safe to re-run: sums are recomputed from a stored anchor, so a repeat import overwrites instead of
  adding up.
- Quarter-hour consumption sensor with the newest complete 15-minute interval of the current local
  day, and the history that a normal recorder retention keeps.
- Delivery points are tracked dynamically: a meter CKW starts to return gets its entities and device
  on the next poll without a reload; a meter that disappears is marked stale, keeps its history and
  becomes available again when CKW reports it.
- Repair issues for a meter exchange, persistent data-quality flags, a publication stalled beyond four
  days, and a recorder retention too short to keep the quarter-hour values.
- The `ckw.backfill` action to re-import daily aggregates as statistics, for a meter swap or a wiped
  recorder database.
- Sensors go unavailable while a poll fails and come back on the next successful one, with a single
  warning and a single recovery line in the log instead of a stream of them.
- German and English UI, translated error and repair messages, and an icon for every entity.
- A local development instance: a Podman runbook, a mock CKW API with a fake Keycloak, a smoke check, a
  mock-versus-production switch by environment variable, and the mise task runner with a task checker
  and a plan-drift CI gate.

### Changed

- The publication delay is described as an observed **1 to 3 days** instead of a fixed one-day lag; a
  lag above 96 hours logs a warning and shows on the consumption sensor as `publication_delay_h`.
- Cost is parsed but not exposed: the CHF amounts of the API (`betrag_ht`, `betrag_nt`) reach no entity
  and no statistic. There are no cost sensors in this release.
- Quarter-hour data is an entity-state sensor, never imported statistics: statistics stay hourly
  because that is what the Energy dashboard reads, and 15-minute values cannot be written into the
  past.

### Fixed

- hassfest refused the manifest until its keys were sorted and `recorder` was declared as an
  after-dependency instead of a hard one; both are corrected.
- The HACS validation job is skipped while the repository is private, because HACS cannot validate a
  private repository and the job failed for that reason alone.
- The Python test harness is pinned to the Home Assistant version the suite is green on, so an
  upstream release cannot turn CI red for reasons that have nothing to do with this integration.

### Docs

- A tutorial that walks from the CKW client id to a populated Energy dashboard, and how-tos for
  installing and removing the integration, keeping quarter-hour history, pairing the archive with a
  local live source, writing a commit and finding the next task.
- Reference pages for the OpenEnergy API and what the live endpoints actually return, the entities,
  the configuration parameters and the `ckw.backfill` action, the crosscutting conventions, the
  glossary and the quality-scale audit.
- Explanations of a decision series from ADR-0001 to ADR-0009, the architecture, the data model, the
  session lifetime and why 15-minute values cannot be imported.
- A documentation map with a quadrant check, Markdown lint and a relative-link check in CI.

### Known limitations

- **Not real-time.** The meter is read once a day and CKW publishes late, so the newest value is 1 to 3
  days old. This integration is an archive, not a monitor; live power needs a local source.
- **No cost entities.** Daily CHF amounts are parsed from the API, but no entity, statistic or
  diagnostics field exposes them.
- **No retroactive 15-minute history.** The quarter-hour sensor starts at installation time and its
  history is bounded by `recorder.purge_keep_days`.
- **No symptom-by-symptom troubleshooting page.** The diagnostics download is the only debugging aid;
  nothing yet walks a failing poll from symptom to fix.
- **A real CKW login is not verified yet.** The browser flow needs a redirect URI registered at CKW for
  your Home Assistant address; until that is in place the flow is covered by tests and the mock only, so
  the first real login is still the first real test. The backfilled sums are likewise not yet compared
  against the charts in the CKW portal.
- **The quality-scale audit is a claim, not a verified fact.** Bronze, Silver and Gold are done or
  exempt and `manifest.json` claims `quality_scale: gold`, but hassfest only checks that file inside
  Home Assistant core; here it is our own audit, kept honest by
  [docs/reference/quality-scale.md](docs/reference/quality-scale.md).
- **Not installable from HACS yet.** HACS cannot install from a private repository. Until the
  repository is public, install by copying `custom_components/ckw/` into your Home Assistant
  configuration, as described in [docs/how-to/install.md](docs/how-to/install.md).

## Maintaining this changelog

How an entry gets written — one line under the group that fits, in the same pull request as the change,
with the page that describes it — is part of the documentation rule in
[CONTRIBUTING.md](CONTRIBUTING.md#keep-the-documentation-honest).

### Releasing

1. Bump `"version"` in `custom_components/ckw/manifest.json`; `hacs.json` carries no version of its own,
   so keep its `name` and `homeassistant` minimum in step with the manifest, and leave its `filename`
   matching the tag if a release asset is ever used.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section above and leave `## [Unreleased]` empty again.
3. Confirm hassfest and the HACS action are green on the release commit and that a fresh Home
   Assistant instance installs and sets up the integration.
4. Tag it: `git tag -a vX.Y.Z -m "vX.Y.Z"` and push the tag.
5. Publish a GitHub release from the tag, using the changelog section as the notes.
6. Only after the repository is public can HACS users install the release: HACS validates and installs
   from public repositories only, so a private repository gives no install path at all.

## Related

- [CONTRIBUTING.md](CONTRIBUTING.md) — the gates, the pull-request checklist and the release duty.
- [docs/reference/quality-scale.md](docs/reference/quality-scale.md) — what is verified today and every
  known gap with the task that closes it.
- [ROADMAP.md](ROADMAP.md) — the user-facing milestones behind the version numbers.
- [GOAL.md](GOAL.md) — scope and non-goals, so a missing feature reads as a decision, not an oversight.
