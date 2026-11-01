# Agent contract

This page answers: what must an AI agent read, run and never do in this repository?

Binding, short, imperative. Humans get the same rules as a checklist in
[CONTRIBUTING.md](CONTRIBUTING.md); the reasoning lives in the documents named below, this page only
states the contract.

## Read first

Read these before the first command, and the ADR before touching code it shaped.

| Read | For |
| --- | --- |
| [PLAN.md](PLAN.md) — "Decisions", "MVP definition", "Cross-cutting gates", "Hard rules" | what the work is, what is already decided, what "done" means |
| [GOAL.md](GOAL.md) | scope and non-goals; do not build outside them |
| [docs/reference/conventions.md](docs/reference/conventions.md) | error mapping, redaction, Europe/Zurich, unique ids, logging, naming |
| [docs/README.md](docs/README.md) | which document owns which subject, so you edit the right one |
| the relevant ADR — [docs/explanation/decisions/README.md](docs/explanation/decisions/README.md) | why the code is shaped the way it is before you reshape it |
| [docs/how-to/commits.md](docs/how-to/commits.md) | commit types and the co-author trailer |

One task, one issue, one branch. The issue number **is** the task identifier; the plan row is
authoritative for scope and dependencies, the issue for progress. When they disagree, fix the plan
first.

## Commands

Every command goes through mise. Never call `python`, `pip`, `pytest`, `ruff`, `pre-commit`, `podman`
or `markdownlint` directly — not in a shell, a script, a workflow or a document.

| Task | Use for |
| --- | --- |
| `mise run setup` | once after clone or pull: pinned toolchain, `.venv` + test dependencies, git hooks, `config/` and `scripts/dev/.env` |
| `mise run lint` | gate G-1, `ruff check` + `ruff format --check` |
| `mise run fmt` | the writing half of G-1 |
| `mise run test` | gate G-2, `pytest` with the coverage floor from `pyproject.toml` |
| `mise run types` | `mypy --strict` over the integration; a CI step, not part of `check` |
| `mise run hooks` | gate G-3, `pre-commit run --all-files`, secret scan included |
| `mise run docs` | Markdown lint, Diátaxis layout, relative links, quality-scale summary |
| `mise run check` | G-1 + docs + G-2 + G-3, then G-6 when the dev container runs; what CI mirrors and what `git push` runs |
| `mise run dev-up` | start the local Home Assistant instance |
| `mise run dev-down` | stop and remove it, keep `config/` |
| `mise run dev-logs` | follow the instance's log |
| `mise run dev-shell` | a shell inside the container |
| `mise run dev-onboard` | owner user and `.dev/token` on a fresh instance |
| `mise run dev-reset` | clean recorder database before statistics and backfill work |
| `mise run dev-smoke` | gate G-6, the instance loads this component without tracebacks |
| `mise run dev-mock` | the mock CKW API and fake Keycloak on port 8787 |
| `mise run next` | which planned task can start now, and where plan and tracker drift |
| `mise run issues` | plan-drift gate: non-zero when `PLAN.md` and the tracker disagree |

Every task runs from the repository root, so a task started in a subdirectory does the same thing as
one started at the top. `mise tasks` prints this table with each task's one-line description.

`mise run setup` is the bootstrap named in [PLAN.md](PLAN.md). `mise run check` skips G-6 with a printed
reason when no dev container is running, so run `mise run dev-smoke` deliberately whenever the component
changed. The dev instance runbook is [scripts/dev/README.md](scripts/dev/README.md); do not invent Podman
commands, the scripts under `scripts/dev/` are the only place that calls `podman`.

The `pre-push` hook runs exactly `mise run check` and refuses the push when it is red (#142).

## Branch, commit, pull request

`main` is branch-protected (#110): a slice lands as a pull request, never as a push to `main` and
never as a local merge. Required checks are *Gates (mise run)*, *Hassfest* and *HACS validation*,
strict (the branch must be up to date with `main` before it merges).

1. `gh issue develop <N> --checkout` (or `git switch -c ckw-<topic>`) — one branch per issue, no
   exceptions, no branch serving two issues. Work on it, and get `mise run check` green.
2. Commit `<type>: <summary>` in the imperative, no trailing period; types in
   [docs/how-to/commits.md](docs/how-to/commits.md). The `Co-Authored-By: pi (<model>)` trailer is
   added by the `commit-msg` hook: never write it by hand, never amend it away, never
   `git commit --no-verify`.
3. `git push -u origin <branch>`.
4. `gh pr create --base main --head <branch> --title "<commit subject>" --body "<what / why / how it
   was verified> — Closes #N"`.
5. Wait for the three required checks; when they are green, merge with
   `gh pr merge --squash --delete-branch`. The body's `Closes #N` closes the issue; the same pull
   request marks the `PLAN.md` row ✅ and failing to mark it fails the plan-drift gate.
6. Never `git push` to `main`, never merge locally, never bypass a check with `--admin`.

The local pre-push gate (`mise run check`) stays the fast inner loop — it catches a red branch in
about 20 s, before the push; the PR checks are the authoritative gate.

## Notify the coordinator

Silence means still working. Two messages, nothing else:

```bash
workmux send ckw-homeassistant "done <handle>: <one-line summary + commit sha>"
workmux send ckw-homeassistant "blocked <handle>: <what blocks it, and what would unblock it>"
```

Send `done` when the pull request is open with its checks green (or merged, when you are the
coordinator), `blocked` as soon as you know you cannot finish under the current contract (missing
credentials, a contradictory plan, a failing external service) — not after you have improvised
around it. Never send progress pings, never send a message per file. `mise run next --notify` uses the
same channel for the scheduled task check.

## Hard rules

1. No API field in code without a fixture or a citation in
   [docs/reference/ckw-openenergy-api.md](docs/reference/ckw-openenergy-api.md).
2. No secret anywhere: not in code, fixtures, tests, logs, commit messages, issues or this
   conversation. Never persist the CKW password, an SMS code, or an access or refresh token.
3. Redact `deliverypointnumber` to its last four characters, and never write a customer id, address
   or token into a fixture, a diagnostics dump or a debug line.
4. Entity unique ids are append-only: never rename, never reuse a retired id. A rename needs an ADR.
5. A new dependency needs an ADR, a `requirements` entry and the `dependency-transparency` rule.
6. A change to `manifest.json`, `strings.json` or `quality_scale.yaml` is verified by hassfest in the
   same pull request — the CI job, not your reading of the schema.
7. A behaviour change ships with a test, the affected Diátaxis page, a `CHANGELOG.md` entry and, for a
   decision, a new ADR. ADRs are append-only; supersede with a new number.
8. Never push to `main`, never merge a branch locally, never merge a pull request whose required
   checks are red or pending, never `--admin`, never force-push a shared branch, never edit `PLAN.md`
   scope to make a change fit.
9. A red gate is a stop. The `pre-push` hook runs `mise run check` and rejects the push when it is red
   (#142), and the three PR checks decide the merge (#159); never `MISE_SKIP_GATE=1`, never
   `git push --no-verify` — the last one also skips the secret scan.

## Definition of done

- `mise run check` green (and `mise run types` green), and `mise run dev-smoke` green when the component
  changed.
- A test for every behaviour change; coverage stays at or above the threshold in `pyproject.toml`.
- The affected page updated, `CHANGELOG.md` updated, the ADR written and its index row added.
- The `PLAN.md` row marked ✅, the issue closed by the merged pull request through `Closes #N`.

## Manual-only zones

These may be drafted, reviewed and tested by an agent, but a human verifies them on a real instance
before the merge. Unattended merges are forbidden here:

- token storage and refresh, including anything that writes a token
- the reauthentication and reconfigure user experience
- statistics import and anything that touches the recorder database
- backfill behaviour that rewrites history

## Related

- [CONTRIBUTING.md](CONTRIBUTING.md) — the same obligations as a human checklist, plus release duty.
- [docs/reference/conventions.md](docs/reference/conventions.md) — the rules restated here in one place.
- [docs/how-to/commits.md](docs/how-to/commits.md) — commit types and the trailer, in detail.
- [PLAN.md](PLAN.md) — gates, hard rules, decisions and the task tables this contract enforces.
