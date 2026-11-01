# Documentation

This page answers: which document do I need?

Diátaxis sorts documentation by the need behind the question: a **tutorial** teaches by doing, a
**how-to** solves one specific problem, a **reference** page lets you look up exact facts, and an
**explanation** helps you understand why something is the way it is. Every page below belongs to
exactly one quadrant; entries marked `planned` are not written yet and name the issue that writes them.

## Start here

- **I want to use it** — [getting-started](tutorial/getting-started.md) → [install](how-to/install.md).
- **I want to change code** — [architecture](explanation/architecture.md) → [conventions](reference/conventions.md)
  → work-with-ai-agents (planned, #98).
- **Something is broken** — [debug a connection](how-to/debug-connection.md) → reauthenticate (planned, #122).

## Tutorial

Learn by doing, start to finish, no alternatives.

| Document | What it covers | Audience |
| --- | --- | --- |
| [getting-started.md](tutorial/getting-started.md) | Install, client id, first CKW login, Zählpunkt choice and a populated Energy dashboard in one pass. | New users |

## How-to

One goal per page, steps only, no theory.

| Document | What it covers | Audience |
| --- | --- | --- |
| `how-to/request-ckw-client.md` (planned, #120) | Asking CKW for the OpenEnergy client id and registering the Home Assistant redirect URI. | New users |
| [install.md](how-to/install.md) | Installing through HACS or by copying the folder, updating, and removing the integration with or without its statistics. | Users |
| `how-to/reauthenticate.md` (planned, #122) | Repairing a login CKW no longer accepts, without losing the imported history. | Users |
| `how-to/backfill-history.md` (planned, #123) | Re-running the history import for a Zählpunkt that came back with gaps. | Users |
| [live-and-archive.md](how-to/live-and-archive.md) | Pairing the CKW archive with a local live source on the meter, and why the two numbers differ. | Users |
| [keep-quarter-hour-history.md](how-to/keep-quarter-hour-history.md) | Keeping the quarter-hour values longer than the recorder's default retention, or exporting them. | Users |
| [debug-connection.md](how-to/debug-connection.md) | Symptom → cause → fix for a stale value, a rejected login, `403`, `429`, an unfinished backfill and missing statistics, plus the log levels and what to attach to a bug report. | Users, contributors |
| `how-to/work-with-ai-agents.md` (planned, #98) | Driving this repository with an AI agent under the contract in `AGENTS.md`. | Contributors, agents |
| [commits.md](how-to/commits.md) | Commit types, the agent co-author trailer and how to find commits by model. | Contributors |
| [next-task.md](how-to/next-task.md) | Finding the task that can start now, and having a timer or crontab remind you. | Maintainers, contributors |

## Reference

Lookup only: exact names, values and limits, with no steps.

| Document | What it covers | Audience |
| --- | --- | --- |
| [ckw-openenergy-api.md](reference/ckw-openenergy-api.md) | The endpoints, fields, rate limits and error codes of the CKW OpenEnergy API as CKW publishes them. | Contributors |
| [ckw-openenergy-api-verification.md](reference/ckw-openenergy-api-verification.md) | What the live endpoints and the deployed API model actually return, and where that contradicts the transcription. | Contributors |
| [fresh-install-check.md](reference/fresh-install-check.md) | The recorded acceptance run on a clean instance: install from the published repository, mock login, entities, statistics, diagnostics, log lines and findings. | Maintainers, contributors |
| [glossary.md](reference/glossary.md) | One definition per CKW and Home Assistant term used anywhere in this repository. | Everyone |
| [entities.md](reference/entities.md) | Every entity and statistic with its unit, device class, unique id, statistic id, cadence and Energy-dashboard eligibility. | Users, contributors |
| [configuration.md](reference/configuration.md) | Every setup and options field with its type, allowed values, default and effect, plus the `ckw.backfill` action and the development-only endpoint overrides. | Users |
| [quality-scale.md](reference/quality-scale.md) | The 51 Bronze, Silver and Gold quality-scale rules, their status, the file or test that proves each, and the open gaps. | Contributors |
| [conventions.md](reference/conventions.md) | The crosscutting rules: error mapping, redaction, Europe/Zurich, unique ids, logging, gates. | Contributors |
| [examples.md](reference/examples.md) | Copy-paste automations, a dashboard card and a load-shifting sketch, each with the entity ids and units it assumes. | Users |

## Explanation

Why it is like this, and which alternatives lost.

| Document | What it covers | Audience |
| --- | --- | --- |
| [architecture.md](explanation/architecture.md) | The system in five arc42 chapters: context, building blocks, one runtime sequence and the ADRs behind each block. | Contributors |
| [data-model.md](explanation/data-model.md) | Why values arrive 1 to 3 days late, what HT/NT, billed quantity and CHF amounts mean, and the known limitations. | Users, contributors |
| [session-lifetime.md](explanation/session-lifetime.md) | How long a CKW login holds, what the 6 h refresh token can and cannot survive, the keep-alive rule the coordinator follows and the realistic target (parity with CKW's own app). | Contributors |
| [quarter-hour-history.md](explanation/quarter-hour-history.md) | The measured answer to whether 15-minute history can be imported as statistics (it cannot), and what that means for the backfill. | Contributors |
| [decisions/README.md](explanation/decisions/README.md) | The ADR index from ADR-0001 to ADR-0009, each recording one decision and the alternatives rejected. | Contributors |

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
- [CONTRIBUTING.md](../CONTRIBUTING.md) — dev setup, gates, the pull-request checklist and the
  release duty.
- [AGENTS.md](../AGENTS.md) — the agent contract: what to read first, the commands, the hard rules.
