# Contributing

This page answers: how do I set up this repository, run the local instance, get a change merged, and
which duties come with it?

Scope first: [GOAL.md](GOAL.md) says what this integration is and is deliberately not, [PLAN.md](PLAN.md)
holds the task tables, the gates and the decisions. Do not build outside that scope; an idea that is
out of scope there needs a plan change first, not a pull request.

## Set up the toolchain

```bash
mise run setup
```

`mise run setup` is the only bootstrap step: it installs the pinned toolchain (`mise install`), creates
`.venv` and installs `requirements_test.txt` into it, installs the `pre-commit`, `commit-msg` and
`pre-push` git hooks, and seeds the gitignored `config/` directory and `scripts/dev/.env` from their
tracked templates. It is idempotent — run it again after a pull or a toolchain bump — and it never starts
the dev container; that stays `mise run dev-up`.

Everything is pinned in `mise.toml` and run through mise. Do not call `python`, `pip`, `pytest`,
`ruff`, `pre-commit`, `podman` or `markdownlint` directly — the pins are what keep CI and your machine
on the same versions. The task list with what each command is for:
[AGENTS.md](AGENTS.md) → "Commands".

## Run Home Assistant with the integration

The instance is disposable: one rootless Podman container `ha-dev`, its state in the gitignored
`config/` directory, nothing installed outside the repository.

```bash
mise run dev-up          # start (or restart) the instance on http://localhost:8123
mise run dev-onboard     # owner user, core config, API token in .dev/token
mise run dev-smoke       # gate G-6: component present, no traceback
mise run dev-logs        # follow the log
mise run dev-reset       # clean recorder database, then start again
mise run dev-down        # stop and remove the container, keep config/
```

Prerequisites (rootless Podman, subordinate UID/GID range, SELinux `:Z`, ports), the mounts, the
upgrade path for the Home Assistant image and a troubleshooting table are in
[scripts/dev/README.md](scripts/dev/README.md). Read it before the first `dev-up`; it is the runbook,
this page is only the entry point.

## Mock or production

The integration talks to the same code path either way — only the endpoints differ, which is why the
mock is a valid test target and why no separate development branch exists in the code.

| You want | Do this |
| --- | --- |
| iterate on entities, statistics, the UI, offline and without a CKW account | `mise run dev-mock` in one shell, then point the instance's `CKW_*` variables at it through `scripts/dev/.env` |
| verify against the real API | leave `scripts/dev/.env` without `CKW_*` overrides, use the client id CKW issued for your redirect URI (`docs/reference/configuration.md`) |

The exact URLs and variables per mode are in
[scripts/dev/README.md](scripts/dev/README.md) → "Mock or production"; copy `scripts/dev/.env.example`
rather than typing them. The mock's tokens are unsigned and unchecked — never present anything from
the mock as authenticated CKW access.

## Gates before a pull request

A pull request is only reviewable when all of this is green, locally and in CI. The gate numbers are
[PLAN.md](PLAN.md) → "Cross-cutting gates".

- [ ] G-1 `mise run lint` — ruff check and format
- [ ] G-2 `mise run test` — pytest, coverage at or above the floor in `pyproject.toml`
- [ ] G-3 `mise run hooks` — `pre-commit run --all-files`, gitleaks included
- [ ] `mise run types` — `mypy --strict` over the integration; a separate CI step, not part of G-1…G-3
- [ ] G-4 hassfest — green in CI; run it in the same pull request that touches `manifest.json`,
      `strings.json`, `icons.json` or `quality_scale.yaml`
- [ ] G-5 HACS action — green in CI once the repository is public
- [ ] G-6 `mise run dev-smoke` — green, and `mise run dev-logs` shows no traceback from
      `custom_components.ckw`
- [ ] G-7 the diff stays near 400 changed lines, and every behaviour change carries a test
- [ ] the affected page under `docs/` is updated, `CHANGELOG.md` has an entry
- [ ] the `PLAN.md` row is marked ✅ and the pull request body says `Closes #N`

`mise run check` runs G-1, the docs check, G-2 and G-3 in one go, then G-6 when the dev container is
running — and prints the reason when it skips it; G-4 and G-5 are separate.

## The pre-push gate

GitHub will not protect a branch on a private repository without a paid plan, so the equivalent gate
runs locally. `.pre-commit-config.yaml` adds a `pre-push` hook that runs `mise run check` and aborts the
push when anything is red, before a commit leaves your machine; the same stage scans the full history
with gitleaks.

- A red gate is not negotiated with. Fix the finding.
- `MISE_SKIP_GATE=1 git push` is the escape hatch and prints a loud warning. It exists for the release
  manager in an emergency, never to land a red branch; run `mise run check` before the merge that
  matters.
- `git push --no-verify` skips the gate *and* the secret scan. Do not use it.
- The gate checks the working tree, so commit or stash first when you want the pushed commit verified.
- Budget: the hook must stay under ~90 s. It takes about 20 s today (`mise run test` is the slowest
  part), so run `mise run check` once before you start a long review round rather than on every push.

## Branches, commits, issues

- One issue per branch: `gh issue develop <N> --checkout`. The issue number is the task identifier.
- Commit `<type>: <summary>`, imperative, no trailing period. Types, examples and the automatic
  `Co-Authored-By: pi (<model>)` trailer: [docs/how-to/commits.md](docs/how-to/commits.md).
- Do not bypass the hooks. `git commit --no-verify` also skips the secret scan.
- The pull request body carries `Closes #N`; merging closes the issue.
- Which task can start now, and where plan and tracker drift apart: `mise run next`, documented in
  [docs/how-to/next-task.md](docs/how-to/next-task.md).

## When an ADR is required

A change needs an architecture decision record when it adds a dependency, touches auth, tokens or
secrets, changes the statistics or recorder strategy, changes the entity-id or unique-id scheme,
changes user-visible behaviour (names, units, polling, backfill), deviates from the plan or an earlier
ADR, or when a review round argued about it.

Write it as `docs/explanation/decisions/NNNN-kebab-case-title.md` from
[0000-template.md](docs/explanation/decisions/0000-template.md), add its row to the index in
[docs/explanation/decisions/README.md](docs/explanation/decisions/README.md), and ship it in the same
pull request as the change. Accepted ADRs are append-only: a changed mind is a new number that
supersedes the old one.

## Keep the documentation honest

A behaviour change updates the affected Diátaxis quadrant (tutorial, how-to, reference or
explanation), adds its `CHANGELOG.md` entry and, for a decision, an ADR. When a page needs a new
neighbour in the map, add it to [docs/README.md](docs/README.md) — the map is the only way to find a
document.

`mise run docs` enforces the layout, the Markdown rules and the relative links in CI, so a misplaced
page or a link into nowhere is a red build, not a nitpick. A document that contradicts the code is a
bug: fix it in the same pull request or open the issue the same day.

## Where to ask, where to report

| You have | Go to |
| --- | --- |
| a question about using the integration, or a bug in it | an issue in [the issue tracker](https://github.com/Maladus/ha-ckw-smartmeter/issues) — say what you did, what you expected and what happened, and paste the `custom_components.ckw` log lines with tokens and your Zählpunkt removed |
| a login that CKW no longer accepts, a 401/403, a client id or redirect URI question | CKW OpenEnergy support, <openenergy@ckw.ch> — this repository cannot grant you API access |
| a security problem | GitHub's private advisory channel for this repository, never a public issue: a leaked token is a security report, not a bug report |
| a feature you miss | check [PLAN.md](PLAN.md) and [ROADMAP.md](ROADMAP.md) first; if it is listed as out of scope, an issue will not change that |

Never paste an access token, a refresh token, an SMS code or your CKW password into an issue, a pull
request, a log excerpt or a commit. Redact the `deliverypointnumber` to its last four characters.

## Releasing

The release procedure itself — version bump, changelog entry, tag, release notes, and the reason HACS
can only install once the repository is public — is written once, in
[CHANGELOG.md](CHANGELOG.md#releasing). Follow it there.

What only a maintainer can add around it: a green hassfest and HACS action run on the release commit,
one fresh-instance install from an empty Home Assistant before the tag is pushed, and the decision on
the public flip. A release is not a merge; it is a separate, deliberate step after the gates.

## Related

- [AGENTS.md](AGENTS.md) — the same obligations for an AI agent, with the command table.
- [docs/README.md](docs/README.md) — the documentation map.
- [docs/reference/conventions.md](docs/reference/conventions.md) — the crosscutting rules every change
  has to respect.
- [CHANGELOG.md](CHANGELOG.md) — released changes and the release procedure.
