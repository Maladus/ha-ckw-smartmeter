# 0002 — Import entity statistics instead of storing history as states

- Status: proposed
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

CKW delivers one aggregate reading per meter per day: cumulative kWh and the CHF amount billed for it. The Energy dashboard needs long history, and the integration is expected to show roughly two years of it right after setup — data the API can serve, but which Home Assistant has never seen. Recorder states are the default storage for sensors and are kept for a configurable purge window, not for two years.

## Decision

The cumulative kWh and CHF sensors import long-term statistics via `async_import_statistics` with `StatisticMetaData(source="recorder", statistic_id=<entity_id>, has_sum=True)`. First setup backfills about 730 days. Every later poll re-imports the rolling 7-day window (ADR-0004), so the same day is written repeatedly and the import is idempotent over the overlap.

## Consequences

**Positive**

- The full two years is visible in the Energy dashboard immediately, with no gap before installation day; statistics survive the recorder purge window while states are cleaned up.
- `source="recorder"` keeps the statistics bound to the entity, so the Energy dashboard picker lists them like any other sensor.

**Negative**

- Sums must be recomputed from the source data on every import, never appended incrementally; an append would double-count the overlapping days.
- A meter exchange invalidates the cumulative series: it needs a new statistic id plus a repair issue for the user (#133), because the sum cannot continue across two physical meters.
- The recorder database grows, slowly: one aggregated row per day per sensor, not 288.

**Follow-ups**

- #62/#87 implement the import and the backfill; #133 covers the meter-exchange repair.
- The observed 1–3 day lag and the "aggregated daily values" caveat are stated in `data-model.md` (#125).

## Alternatives considered

**External statistics (`source=DOMAIN`).** Valid for data that has no entity, but the Energy dashboard picker only offers entities — an external statistic cannot be selected there, and the sensors would have to exist anyway for the UI. Rejected as the wrong tool for a meter entity.

**Plain sensor history only.** Two years of backfill would mean two years of recorder states, one row per poll per sensor, all of it purged eventually — the opposite of what long-term statistics are for. Rejected on size and on retention.

**A `last_reset` sensor alone.** Correct for the live value, but it carries no history from before the integration was installed, so the dashboard would start empty and stay sparse for two years. Rejected as a solution; the sensors still expose `last_reset` for the current total.

## Links

- Issues #62, #87 (import), #133 (meter exchange repair), #125 (data model docs)
- ADR-0003 — the entity/unique-id scheme the `statistic_id` follows; ADR-0004 — the window that makes re-import idempotent
