# Documentation

This page answers: which document do I need?

Diátaxis sorts documentation by the need behind the question: a **tutorial** teaches by doing, a
**how-to** solves one specific problem, a **reference** page lets you look up exact facts, and an
**explanation** helps you understand why something is the way it is. Every page below belongs to
exactly one quadrant; entries marked `planned` are not written yet and name the issue that writes them.

## Start here

- **I want to use it** — getting-started (planned, #119) → install (planned, #121).
- **I want to change code** — [architecture](explanation/architecture.md) → conventions (planned, #129)
  → work-with-ai-agents (planned, #98).
- **Something is broken** — debug-connection (planned, #99) → reauthenticate (planned, #122).

## Tutorial

Learn by doing, start to finish, no alternatives.

| Document | What it covers | Audience |
| --- | --- | --- |
| `tutorial/getting-started.md` (planned, #119) | HACS install, first CKW login, Zählpunkt choice and a populated Energy dashboard in one pass. | New users |

## How-to

One goal per page, steps only, no theory.

| Document | What it covers | Audience |
| --- | --- | --- |
| `how-to/request-ckw-client.md` (planned, #120) | Asking CKW for the OpenEnergy client id and registering the Home Assistant redirect URI. | New users |
| `how-to/install.md` (planned, #121) | Installing, updating and removing the integration through HACS. | Users |
| `how-to/reauthenticate.md` (planned, #122) | Repairing a login CKW no longer accepts, without losing the imported history. | Users |
| `how-to/backfill-history.md` (planned, #123) | Re-running the history import for a Zählpunkt that came back with gaps. | Users |
| `how-to/debug-connection.md` (planned, #99) | Reading the log, the diagnostics export and the error codes when polling fails. | Users, contributors |
| `how-to/work-with-ai-agents.md` (planned, #98) | Driving this repository with an AI agent under the contract in `AGENTS.md`. | Contributors, agents |
| [commits.md](how-to/commits.md) | Commit types, the agent co-author trailer and how to find commits by model. | Contributors |

## Reference

Lookup only: exact names, values and limits, with no steps.

| Document | What it covers | Audience |
| --- | --- | --- |
| [ckw-openenergy-api.md](reference/ckw-openenergy-api.md) | The endpoints, fields, rate limits and error codes of the CKW OpenEnergy API as CKW publishes them. | Contributors |
| [ckw-openenergy-api-verification.md](reference/ckw-openenergy-api-verification.md) | What the live endpoints and the deployed API model actually return, and where that contradicts the transcription. | Contributors |
| [glossary.md](reference/glossary.md) | One definition per CKW and Home Assistant term used anywhere in this repository. | Everyone |
| `reference/entities.md` (planned, #124) | Every entity and statistic with its unit, device class and update behaviour. | Users, contributors |
| `reference/configuration.md` (planned, #106) | The parameters a user sets at setup and later in the options flow. | Users |
| `reference/quality-scale.md` (planned, #102) | The quality-scale rules, their tier, and the document or code that satisfies each. | Contributors |
| `reference/examples.md` (planned, #128) | Copy-paste automations, dashboard cards and template sensors built on the integration. | Users |
| `reference/conventions.md` (planned, #129) | The crosscutting rules: error mapping, redaction, Europe/Zurich, unique ids, logging. | Contributors |

## Explanation

Why it is like this, and which alternatives lost.

| Document | What it covers | Audience |
| --- | --- | --- |
| [architecture.md](explanation/architecture.md) | The system in five arc42 chapters: context, building blocks, one runtime sequence and the ADRs behind each block. | Contributors |
| [data-model.md](explanation/data-model.md) | Why values stop at yesterday, what HT/NT, billed quantity and CHF amounts mean, and the known limitations. | Users, contributors |
| [session-lifetime.md](explanation/session-lifetime.md) | How long a CKW login holds, what the 6 h refresh token can and cannot survive, the keep-alive rule the coordinator follows and the realistic target (parity with CKW's own app). | Contributors |
| [decisions/README.md](explanation/decisions/README.md) | The ADR index from ADR-0001 to ADR-0007, each recording one decision and the alternatives rejected. | Contributors |

## Conventions

- One document belongs to exactly one quadrant; if a page starts explaining *why* in the middle of a
  procedure, that explanation moves to `explanation/` and is linked.
- Every page opens with a "This page answers: …" line and ends with a `Related` block linking its
  neighbours in the other quadrants.
- Markdown only, no AsciiDoc and no front matter.
- A document that contradicts the code is a bug — fix it in the same pull request.

## Related

- [PLAN.md](../PLAN.md) — what the work is, task by task, with gates.
- [GOAL.md](../GOAL.md) — why the project exists and what it deliberately is not.
- `CONTRIBUTING.md` (planned, #20) — dev setup, gates and the pull-request checklist.
