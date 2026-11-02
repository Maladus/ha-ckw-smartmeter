# Drive this repository with a coding agent

This page answers: how do I point a coding agent at this repository and get a reviewed pull request out
of it, instead of a pile of unreviewed edits?

The agent is a worker under the same contract as a human, and that contract is [AGENTS.md](../../AGENTS.md).
This page is the operating manual around it: what the agent reads first, the loop it runs, how it
reports, and where a human still has the last word.

## What the agent reads before the first command

| Read | Because |
| --- | --- |
| [AGENTS.md](../../AGENTS.md) | the binding contract: commands, the PR flow, the hard rules, the notification contract |
| [PLAN.md](../../PLAN.md) — "Decisions", "MVP definition", "Cross-cutting gates", "Hard rules" | what is already decided, what "done" means, and which gate proves it |
| [GOAL.md](../../GOAL.md) | scope and non-goals; a change outside them needs a plan change first |
| [docs/reference/conventions.md](../reference/conventions.md) | error mapping, redaction, Europe/Zurich, unique ids, logging, naming — one statement per rule |
| [docs/README.md](../README.md) | which document owns which subject, so the edit lands on the right page |
| the relevant ADR — [decisions/README.md](../explanation/decisions/README.md) | why the code is shaped the way it is, before it is reshaped |

A prompt that names the issue number and those files is enough; the rest the agent looks up itself. Do
not paste the rules into the prompt — a copy of [AGENTS.md](../../AGENTS.md) in a chat message is stale the
moment the contract changes.

## The task loop

One issue, one branch, one pull request, one commit where the change is one idea.

1. **Pick the task.** `mise run next` lists what can start (STARTABLE), what waits (BLOCKED) and where
   plan and tracker disagree (DRIFT). Prefer an issue labelled `status: ready`. The issue number is the
   task identifier and the `PLAN.md` row is authoritative for scope — see
   [next-task.md](next-task.md).
2. **Branch.** `gh issue develop <N> --checkout`. One branch per issue; a second issue gets a second
   branch, never an addition to the first.
3. **Implement.** Read the code the task touches and the ADR behind it. A behaviour change ships with a
   test, the affected page under `docs/` and a `CHANGELOG.md` entry; a decision adds an ADR.
4. **Run the gate.** `mise run check` (lint, docs, pytest, pre-commit — and `dev-smoke` when the dev
   container runs), plus `mise run types` and `mise run dev-smoke` when the component changed. A red gate
   is a stop, not an obstacle to route around.
5. **Commit.** `<type>: <summary>`, imperative, no trailing period; types in [commits.md](commits.md). The
   `Co-Authored-By: pi (<model>)` trailer is written by the `commit-msg` hook from the session — never type
   it, never amend it away, never commit with `--no-verify`.
6. **Push.** `git push -u origin <branch>`. The `pre-push` hook runs exactly `mise run check` and refuses
   a red branch before it ever reaches GitHub.
7. **Open the pull request.**

   ```bash
   gh pr create --base main --head <branch> \
     --title "<the commit subject>" \
     --body "<what changed, why, how it was verified> — Closes #N"
   ```

8. **Merge.** Wait for the three required checks (*Gates (mise run)*, *Hassfest*, *HACS validation*) and
   merge with `gh pr merge --squash --delete-branch`. `Closes #N` closes the issue. The same pull request
   marks the task's `PLAN.md` row ✅; an unmarked row is plan drift and `mise run issues` reports it.

An agent that was spawned by a coordinator stops at step 7 and reports; the merge is the coordinator's or
the maintainer's step. An agent working alone for a human does all eight, and never merges past a red or
pending check.

The ✅ belongs in the pull request, which means `mise run issues` reports those rows as
`done-row-open-issue` until the merge closes the issues. That entry is the expected state of a reviewed
branch, not drift to repair: closing the issue by hand to silence it would leave the work undone and the
row wrong.

## The notification contract

Silence means still working. Exactly two messages, both on the coordinator's channel:

```bash
workmux send ckw-homeassistant "done <handle>: <one line + commit sha + PR url>"
workmux send ckw-homeassistant "blocked <handle>: <what blocks it, and what would unblock it>"
```

- `done` goes out when the pull request is open with its checks green — not when the branch is pushed.
- `blocked` goes out the moment the agent knows it cannot finish: missing credentials, a contradictory
  plan row, a failing external service. Not after it improvised around the problem.
- No progress pings, no message per file, no "starting now". A coordinator that hears nothing assumes the
  handle is still working.
- The coordinator tracks every handle it spawned until that branch is merged or the handle is removed, and
  chases a silent handle instead of assuming the task is finished.

## What an agent must never do

| Never | Because |
| --- | --- |
| push to `main`, merge a branch locally, or run `gh pr merge --admin` | `main` is branch-protected; the pull request checks are the only merge decision |
| bypass a gate: `MISE_SKIP_GATE=1`, `git commit --no-verify`, `git push --no-verify` | the last one also skips the secret scan; a red gate is a finding, not an inconvenience |
| edit a fixture, a test assertion or an allowlist to turn a red test green | the fixtures are the contract; a failing test is either a bug in the code or in the fixture comment that has to be corrected on purpose |
| invent an API field | no field in code without a fixture or a citation in [ckw-openenergy-api.md](../reference/ckw-openenergy-api.md) |
| store a secret anywhere | no CKW password, SMS code, access or refresh token in code, a fixture, a test, a log, an issue, a commit message — or in the conversation with the agent |
| rename, reuse or recycle an entity unique id | ids are append-only; a rename needs an ADR (hard rule 4) |
| edit `PLAN.md` scope so the change fits | the plan is the reviewed scope; an idea that does not fit needs an issue and a plan change |
| invent a `podman` command | `scripts/dev/` is the only place that calls Podman; use `mise run dev-*` |
| write a raw `deliverypointnumber`, a customer id or an address anywhere | the Zählpunkt appears only as its last four characters; a customer id and an address never (hard rule 3) |

## Guardrails that keep the work honest

- **The mock, not CKW, for anything that would need an account.** `mise run dev-mock` serves the OpenEnergy
  API and a fake Keycloak on port 8787, and `mise run dev-env-mock` points the instance at it. Its tokens
  are unsigned fakes: never present anything from the mock as authenticated CKW access, and never give an
  agent a real client id, password or token to "just try it".
- **Fixtures are the contract.** `tests/fixtures/` is synthetic and obviously fake; a field that is not in a
  fixture is not in the code. When the real API has to be measured, a human captures the value, redacts it,
  and commits the fixture — that commit is the citation.
- **`mise run dev-reset` before statistics work.** Import and backfill only behave deterministically on an
  empty recorder database; a test that passes on a dirty one proves nothing.
- **Manual-only zones.** Token storage and refresh, the reauth and reconfigure experience, statistics import
  and anything touching the recorder, and backfill that rewrites history: an agent may draft, review and
  test these, a human verifies them on a real instance before the merge. Unattended merges are forbidden
  there.
- **The docs are part of the change.** A page that contradicts the code is a bug in the same pull request,
  and `mise run docs` fails a broken link or a misplaced page.

## A prompt template for one task

Copy this per task and fill in the blanks; one prompt, one issue, one handle.

```text
Repository: ckw — Home Assistant custom integration, worktree <path>, branch ckw-<topic>
Task: issue #<N> — <issue title>

Read first: AGENTS.md, PLAN.md ("Decisions", "MVP definition", "Cross-cutting gates", "Hard rules"),
docs/reference/conventions.md, docs/README.md, and the ADR that shaped the code you touch.

Scope: <what to change>. Leave alone: <what is out of scope — other issues, PLAN.md scope, the public API>.

Done when: <the "Done when" cell of the PLAN.md row, verbatim>

Gates: `mise run check` green, `mise run types` green,
<mise run dev-smoke whenever custom_components/ changed>.

Commits: one conventional commit, imperative, no trailing period; the co-author trailer is automatic.
Pull request: one per issue, body ends with "Closes #<N>", do not merge it.

Report `done <handle>: <summary + commit sha + PR url>` to ckw-homeassistant when the PR is green, or
`blocked <handle>: <reason + what would unblock>` as soon as you know you cannot finish.

Never: push to main, skip a gate, edit a fixture or a test to make it pass, store a secret anywhere,
rename an entity unique id, or invent an API field without a fixture or a spec citation.
```

## Related

- [AGENTS.md](../../AGENTS.md) — the contract this page walks through, and the command table.
- [Conventions](../reference/conventions.md) — the crosscutting rules an agent must not restate but respect.
- [Write a commit](commits.md) — commit types and the automatic co-author trailer, with examples.
- [Find the next task](next-task.md) — STARTABLE, BLOCKED and DRIFT, and the timer that pings the coordinator.
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — the same obligations as a checklist for a human.
- [PLAN.md](../../PLAN.md) — task tables, gates, hard rules and the decisions behind them.
