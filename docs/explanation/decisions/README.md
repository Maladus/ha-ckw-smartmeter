# Architecture decision records

This page answers: which decisions shaped this integration, what their status is, and where the
reasoning lives.

An ADR records one decision and the options that lost. It is history, not specification: what the
integration does today is documented in `docs/reference/` and `docs/how-to/`, and an ADR is only
consulted when the reason matters (→ ADR-0002). `0000-template.md` holds the skeleton; copy it, do
not edit it.

| No. | Title | Status | Date |
|---|---|---|---|
| [0001](0001-auth-oauth2-vs-pasted-code.md) | Authenticate with OAuth2 authorization code and PKCE | accepted (partially superseded by 0006) | 2026-11-01 |
| [0002](0002-statistics-strategy.md) | Import entity statistics instead of storing history as states | proposed | 2026-11-01 |
| [0003](0003-meter-device-mapping.md) | One device per delivery point, unique ids from the config entry | proposed | 2026-11-01 |
| [0004](0004-poll-interval-and-data-lag.md) | Poll every two hours over a rolling seven-day window | proposed | 2026-11-01 |
| [0005](0005-doc-structure-diataxis-arc42-lite.md) | Documentation in Diataxis, architecture in five arc42 chapters | proposed | 2026-11-01 |
| [0006](0006-auth-measured-constraints.md) | Restrict authentication to the browser authorization-code flow | accepted | 2026-11-01 |
| [0007](0007-token-lifetime-and-keepalive.md) | Keep the session alive with the poll, reauthenticate only on a dead refresh token | proposed | 2026-11-01 |

## Lifecycle

`proposed` while the pull request is open → `accepted` once it is merged (the status field is flipped
in the same PR) → `deprecated`, or `superseded by NNNN` when a later ADR replaces it.

Accepted ADRs are never rewritten. A typo may be fixed; a changed mind needs a new, higher number
that supersedes the old one, and the old one keeps its number, its content and its links so the
reasoning stays traceable. A new decision is required for a new dependency, anything touching auth,
tokens or secrets, the statistics strategy, the unique-id scheme, user-visible behaviour (naming,
units, polling, backfill) or a deviation from `PLAN.md`.

## Related

- `../architecture.md` (#92) — the system as it is today; each building block links back to the ADRs
  that shaped it
- `../data-model.md` (#125) — constraints and their decisions
- `../../README.md` (#27) — the map into the four Diataxis quadrants
