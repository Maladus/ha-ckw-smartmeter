# Changelog

All notable changes to the CKW Smart Meter integration are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Versions before `1.0.0` may change
user-visible behaviour in a minor release; every such change is listed.

## [Unreleased]

### Added

- [How-to: work with an AI agent](docs/how-to/work-with-ai-agents.md): the mechanics of handing one issue to
  a coding agent — what it reads first, the branch → gate → commit → pull request → merge loop, the
  `workmux` done/blocked notification contract, the things it must never do, the mock and fixture
  guardrails, and a prompt template to copy (#98).
- [Reference: redirect URIs](docs/reference/oauth-redirects.md): the three values CKW can have registered
  (My Home Assistant, own FQDN, localhost), what each one depends on, the exact-match rule with the German
  `Ungültiger Parameter: redirect_uri` error, what the My Home Assistant page asks on first use, and the
  troubleshooting for a mismatch. The My Home Assistant URL was previously described as a Cloud feature; it
  works for any installation and needs no Nabu Casa subscription (#41).

### Changed

- The development Home Assistant is pinned to `2026.10.4` in `scripts/dev/.env.example` instead of the
  moving `stable` tag, so `mise run dev-up` cannot upgrade the instance without a commit; the bump
  procedure is in `scripts/dev/README.md` → "Bumping the dev image" (#38). Development-only, no effect on
  an installation.

### Security

- A `Secrets` workflow runs `gitleaks detect --redact` over the full history on every push to `main`, every
  pull request and weekly, next to the local hooks and GitHub push protection. The three layers and the
  rotate-first policy are in [SECURITY.md](SECURITY.md) (#118).

## [0.1.0] - 2026-11-01

The first release. It is exercised against the mock CKW API, the recorded fixtures and the local Home
Assistant instance, and it was installed from an empty Home Assistant the way a stranger would
([fresh-install verification](docs/reference/fresh-install-check.md)); a login with a real CKW account
is not verified yet, see "Known limitations".

### Added

- Login with the CKW Single Sign-On account through the browser, as a public OAuth2 client with
  PKCE and no secret; the access token is refreshed silently and the refresh token is kept on the
  instance, never in the log.
- Setup probes the account before creating the entry and aborts with a translated reason when the
  client is unknown, has no audience, or CKW cannot be reached.
- One config entry per CKW account, so several accounts run side by side, with a Zählpunkt (delivery
  point) picker when the account has more than one electricity meter; gas meters are never offered.
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
- A redacted diagnostics download on the entry's page, for a bug report: version, options, one row per
  delivery point with only its last four characters, the coordinator's health (last poll, exception
  type, rate limit), the statistics progress and two token booleans. No token, no customer number
  (only a short hash), no address and no e-mail address ever reach the file.
- A local development instance: a Podman runbook, a mock CKW API with a fake Keycloak, a smoke check,
  and `mise run dev-env-mock` / `mise run dev-env-prod` to switch the instance between the mock and the
  production CKW endpoints in one command, leaving the rest of `scripts/dev/.env` alone.
- `mise run setup` bootstraps a fresh clone in one step: the pinned toolchain, the `.venv` the gates
  use, the `pre-commit`, `commit-msg` and `pre-push` git hooks, and the gitignored `config/` and
  `scripts/dev/.env`. Every workflow is a documented mise task that runs from the repository root, CI
  calls only those tasks, and a task checker with a CI plan-drift gate keeps `PLAN.md` and the issue
  tracker in step.
- A `pre-push` hook that runs `mise run check` and refuses a red push, with `MISE_SKIP_GATE=1` as the
  documented escape hatch and `git push --no-verify` refused: the local stand-in for the server-side
  checks.
- Issue forms for a bug report and a feature request, a pull-request checklist mirroring the gates, a
  Contributor Covenant 2.1 code of conduct, a security policy with the private reporting channels, a
  `SUPPORT.md` that says where to ask instead of report, and a `CODEOWNERS` naming the single
  maintainer.

### Changed

- The repository is public and `main` is branch-protected. A change reaches it through a pull request
  with three required checks — *Gates (mise run)*, *Hassfest* and *HACS validation* — instead of a
  local fast-forward: the branch is pushed, the pull request body says what changed, why and how it
  was verified plus `Closes #N`, and it is merged with `gh pr merge --squash --delete-branch`. The
  local `mise run check` pre-push hook stays the fast inner loop in front of those checks.
- HACS can install the release, because the repository is public: add
  `https://github.com/Maladus/ha-ckw-smartmeter` as a custom repository of category **Integration**.
  Being listed in the HACS default list is a separate submission.
- `hacs.json` asks for Home Assistant 2025.8 or newer, the version the README, the tutorial and the
  local dev image name; it said 2025.1 before, which nothing was tested against.
- The publication delay is described as an observed **1 to 3 days** instead of a fixed one-day lag; a
  lag above 96 hours logs a warning and shows on the consumption sensor as `publication_delay_h`.
- Cost is parsed but not exposed: the CHF amounts of the API (`betrag_ht`, `betrag_nt`) reach no entity
  and no statistic. There are no cost sensors in this release.
- Quarter-hour data is an entity-state sensor, never imported statistics: statistics stay hourly
  because that is what the Energy dashboard reads, and 15-minute values cannot be written into the
  past.

### Fixed

- `ckw.backfill` is registered when the domain loads, so the action exists before any config entry is
  set up, and it refuses with a translated error when there is nothing to import.
- hassfest refused the manifest until its keys were sorted and `recorder` was declared as an
  after-dependency instead of a hard one; both are corrected.
- The HACS validation job no longer fails on a repository it cannot reach: it was skipped while the
  repository was private and runs again, green, now that it is public.
- The Python test harness is pinned to the Home Assistant version the suite is green on, so an
  upstream release cannot turn CI red for reasons that have nothing to do with this integration.

### Docs

- A tutorial that walks from the CKW client id to a populated Energy dashboard; how-tos for requesting
  the client id, installing and removing the integration, reauthenticating, re-running the backfill,
  keeping quarter-hour history, pairing the archive with a local live source, debugging a failing poll
  from symptom to fix, writing a commit and finding the next task.
- Reference pages for the OpenEnergy API and what the live endpoints actually return, the entities, the
  configuration parameters and the `ckw.backfill` action, the crosscutting conventions, the glossary,
  the quality-scale audit, copy-paste automation examples and the recorded fresh-install verification.
- Explanations of a decision series from ADR-0001 to ADR-0009, the architecture, the data model, the
  session lifetime and why 15-minute values cannot be imported.
- A documentation map with a quadrant check, Markdown lint and a relative-link check in CI.
- `AGENTS.md` as the contract for an AI agent, `CONTRIBUTING.md` for a human contributor, this changelog
  and the HACS store blurb.

### Known limitations

- **Not real-time.** The meter is read once a day and CKW publishes late, so the newest value is 1 to 3
  days old. This integration is an archive, not a monitor; live power needs a local source.
- **No cost entities.** Daily CHF amounts are parsed from the API, but no entity, statistic or
  diagnostics field exposes them.
- **No retroactive 15-minute history.** The quarter-hour sensor starts at installation time and its
  history is bounded by `recorder.purge_keep_days`.
- **A real CKW login is not verified yet.** The browser flow needs a redirect URI registered at CKW for
  your Home Assistant address; until that is in place the flow is covered by tests and the mock only, so
  the first real login is still the first real test. The backfilled sums are likewise not yet compared
  against the charts in the CKW portal.
- **The quality-scale audit is a claim, not a verified fact.** Bronze, Silver and Gold are done or
  exempt and `manifest.json` claims `quality_scale: gold`, but hassfest only checks that file inside
  Home Assistant core; here it is our own audit, kept honest by
  [docs/reference/quality-scale.md](docs/reference/quality-scale.md).
- **Not in the HACS default list.** Installing takes the custom-repository step above; the submission
  to `hacs/default` is a separate pull request that follows a few weeks of real-world use.

## Maintaining this changelog

How an entry gets written — one line under the group that fits, in the same pull request as the change,
with the page that describes it — is part of the documentation rule in
[CONTRIBUTING.md](CONTRIBUTING.md#keep-the-documentation-honest).

### Releasing

The checklist behind these steps, and what "ready to release" means here, is
[docs/reference/release-checklist.md](docs/reference/release-checklist.md).

1. Bump `"version"` in `custom_components/ckw/manifest.json`; `hacs.json` carries no version of its own,
   so keep its `name` and `homeassistant` minimum in step with the README, and leave its `filename`
   matching the tag if a release asset is ever used.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section above and leave `## [Unreleased]` empty again.
3. Confirm hassfest and the HACS action are green on the release commit.
4. Merge the release pull request, then tag the merge commit: `git tag -a vX.Y.Z -m "X.Y.Z"` and
   `git push origin vX.Y.Z`.
5. Publish a GitHub release from the tag, using the changelog section as the notes.
6. Re-run the fresh-install check against the tag: a stranger installs the release from HACS and reaches
   a populated Energy dashboard.
7. The submission to the HACS default list (`hacs/default`) follows after a few weeks of real-world use,
   not on release day.

## Related

- [CONTRIBUTING.md](CONTRIBUTING.md) — the gates, the pull-request checklist and the release duty.
- [docs/reference/release-checklist.md](docs/reference/release-checklist.md) — what has to be true
  before a tag is pushed.
- [docs/reference/quality-scale.md](docs/reference/quality-scale.md) — what is verified today and every
  known gap with the task that closes it.
- [ROADMAP.md](ROADMAP.md) — the user-facing milestones behind the version numbers.
- [GOAL.md](GOAL.md) — scope and non-goals, so a missing feature reads as a decision, not an oversight.
