# Conventions

This page answers: which rules apply everywhere in this codebase?

One statement per rule, made here and nowhere else — other documents link instead of restating. Every
rule is either checkable by a gate or marked **review only**. arc42 §8, see [architecture](../explanation/architecture.md).

## Error mapping

`api.py` raises typed exceptions and imports nothing from `homeassistant`; `coordinator.py` is the only
translator. A raw status code or response body never reaches the UI.

| Failure | `api.py` raises | `coordinator.py` maps to | Enforced by |
|---|---|---|---|
| `401`, `403` | `CkwAuthError` | `ConfigEntryAuthFailed` → reauth | test per status code (#63), per case (#70) |
| `429` | `CkwRateLimitError`, reset from `x-ratelimit-reset` (ms) | `UpdateFailed`, reset time in the message | same |
| `400`, `408`, `500`, `503` | `CkwApiError` | `UpdateFailed` | same |
| connection error, timeout | `aiohttp.ClientError`, `TimeoutError` | `ConfigEntryNotReady` at setup, `UpdateFailed` at runtime | same |

User-visible text comes from `strings.json` `exceptions:`, raised as `HomeAssistantError` with
`translation_domain` and `translation_key`, de and en — never an English literal in code (#130); enforced
by hassfest and by tests that assert keys rather than strings. Related: ADR-0001.

## Redaction

| Rule | Enforced by |
|---|---|
| Never log or commit an access token, a refresh token, an SMS code or the CKW password | gitleaks `ckw-*-token` rules, `detect-private-key`, `no-dev-artifacts` (gate G-3) |
| A `deliverypointnumber` appears only as its last 4 characters; customer id and addresses never at all | `diagnostics.py`, review only |
| `diagnostics.py` redacts exactly this set — mask values, do not drop keys (#105) | `diagnostics.py`, dump inspected by hand |
| Fixtures are synthetic and obviously fake, e.g. `CH0000000000000000000000000000001` | `tests/fixtures/`, gitleaks allowlist matches `CH0{10,}` |

## Time

| Rule | Enforced by |
|---|---|
| All date arithmetic happens in `Europe/Zurich`, with `ZoneInfo("Europe/Zurich")` resolved in `const.py` and never a bare string; request path dates are `YYYYMMDD` in local time (`datum_von`/`datum_bis`) | `const.py`, `api.py`, tests |
| Responses are `dateTimeOnly` UTC: convert before bucketing, never bucket by the UTC date, and emit one statistic bucket per **local** day — including the 23 h and 25 h DST days | `statistics.py`, DST fixture (#136) |

## Identity

| Rule | Enforced by |
|---|---|
| Entity unique id is `f"{entry_id}_{deliverypointnumber}_{key}"` | `entity.py`, tests (#76) |
| Unique ids are append-only: never rename, never reuse a retired id; a rename requires an ADR | review only (hard rule 4) |
| One device per delivery point, linked to the service device through `via_device`; new and vanished delivery points are handled inside a coordinator run, never by editing ids | `entity.py`, `coordinator.py`, ADR-0003 (#134) |
| Statistic ids derive from the unique id, so a broken id splits the history | `statistics.py`, ADR-0002 |

## Logging

| Situation | Level | Enforced by |
|---|---|---|
| Logger per module, `_LOGGER = logging.getLogger(__name__)` | — | review only |
| First failure of a poll | warning, once | review only, behaviour observed in #103 |
| Recovery after a failure | info, once | same |
| Repeated failures with the same cause | debug | same |
| Per-interval values, every poll | never above debug | review only |

## Polling and caching

| Rule | Enforced by |
|---|---|
| One `DataUpdateCoordinator` owns all I/O and nothing else opens a session; entities never call the API, they read coordinator data | `coordinator.py`, `sensor.py`, review only |
| Interval comes from the options flow — default 2 h, minimum 1 h | `const.py`, `config_flow.py`, tests |
| Each poll refetches a rolling 7-day window; older gaps change only through the backfill | `coordinator.py`, ADR-0004 |
| `PARALLEL_UPDATES = 0` per platform, with a one-line reason | `sensor.py` (#135) |

## Naming and layout

| Rule | Enforced by |
|---|---|
| Domain is `ckw`, the component lives in `custom_components/ckw/` | `manifest.json`, hassfest |
| Modules are exactly `__init__.py`, `const.py`, `api.py`, `coordinator.py`, `entity.py`, `sensor.py`, `statistics.py`, `config_flow.py`, `application_credentials.py`, `diagnostics.py` | hassfest, review only |
| Every constant, URL and tunable lives in `const.py`; no magic string or number at the call site | review only |
| `from __future__ import annotations` and full type annotations in every module, layering `const` → `api` → `coordinator` → `entity`/`sensor`/`statistics` with no upward import | `mypy --strict` (#138), review only |

## Quality gates

| Gate | Command | Proves |
|---|---|---|
| G-1 | `mise run lint` | `ruff check` and `ruff format` clean |
| G-2 | `mise run test` | `pytest` green, coverage threshold (#136) |
| G-3 | `mise run hooks` | `pre-commit run --all-files`, secret scan included |
| G-4 | hassfest (CI) | `manifest.json`, `strings.json`, `quality_scale.yaml` valid |
| — | `mise run docs` | `markdownlint-cli2` plus quadrant and link check (#127) |
| G-6 | `mise run dev-smoke` | the dev instance loads the component without tracebacks (#42) |

A change is done only with a test **and** with its `PLAN.md` row updated — review only. `mise run check`
runs lint, docs, test, hooks and dev-smoke together; task names and pins live in `PLAN.md` §Toolchain and
tasks, and `mise.toml` gains the remaining tasks in #117.

## References

- [Architecture](../explanation/architecture.md) — the modules these rules sit in.
- [Data model](../explanation/data-model.md) — local-day buckets, HT/NT, quality flags.
- [ADR-0001](../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md) — OAuth2 + PKCE, token storage.
- [ADR-0002](../explanation/decisions/0002-statistics-strategy.md) — statistic ids, idempotent import.
- [ADR-0003](../explanation/decisions/0003-meter-device-mapping.md) — one device per delivery point.
- [ADR-0004](../explanation/decisions/0004-poll-interval-and-data-lag.md) — 2 h interval, 7-day window.
- [ADR-0005](../explanation/decisions/0005-doc-structure-diataxis-arc42-lite.md) — why this page exists.
- Not written yet: `docs/how-to/commits.md` (#143), `docs/how-to/work-with-ai-agents.md` (#98).

## Related

- [OpenEnergy API reference](ckw-openenergy-api.md) — the status codes, fields and headers above.
- [Glossary](glossary.md) — `deliverypointnumber`, statistic id, HT/NT.
- [Entities](entities.md) — where the unique-id and device rules become sensors.
- [Quality scale](quality-scale.md) — which rule of this page backs which quality-scale item.
- [PLAN.md](../../PLAN.md) — hard rules, gates and the task behind every number above.
