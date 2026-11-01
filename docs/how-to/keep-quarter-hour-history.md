# Keep the quarter-hour history longer

This page answers: how do I stop Home Assistant from deleting the 15-minute values of the
quarter-hour sensor after a few days?

The short answer: the values are entity **states**, not statistics, so their lifetime is exactly
`recorder.purge_keep_days`. Raise that setting or export the states to an external store before the
recorder purges them — the integration cannot keep them for you.

## Why the values disappear

The [quarter-hour sensor](../reference/entities.md) reports the newest complete 15-minute interval per
poll. Home Assistant refuses to import anything finer than an hour as a long-term statistic, so these
values are written as ordinary state changes and purged with the rest of the recorder history
([ADR-0009](../explanation/decisions/0009-resolution-and-storage.md)). The default retention is
**10 days**. Nothing in the integration can restore an interval once the recorder dropped it: the CKW
API only serves the current day's quarter hours.

## Option A — raise `recorder.purge_keep_days`

1. Open `configuration.yaml` and find (or add) the `recorder:` block.
2. Set the retention above the default, for example 90 days, and restart Home Assistant:

   ```yaml
   recorder:
     purge_keep_days: 90
   ```

3. Confirm the value in the integration's repair issue: it disappears once the retention reaches the
   recommended 30 days, or check the recorder's own settings page.

The setting is **global**: it keeps every entity's states that long, not only the quarter-hour sensor,
so the database grows with everything you record. As a rough order of magnitude, one meter produces
96 quarter-hour states per day, about 35 000 per year; with attributes and indexes that is a few
megabytes per meter and year, on top of the states of all other entities.

## Option B — export the states to an external store

If you want years of 15-minute values instead of months, let the recorder keep its default retention
and feed the states to InfluxDB or another external store (for example through Home Assistant's
InfluxDB integration or an automation that calls the store's write service).

1. Pick the store and create its database or bucket for the meter values.
2. Exclude the quarter-hour sensor from the recorder if you do not need the states in Home Assistant
   itself, so the same data is not stored twice.
3. Start exporting **before** the retention window closes; nothing can be exported retroactively.

The supported path for *past* quarter-hour history — before this integration was installed — is a
separate, paced backfill against the CKW API into the same external store; it is not part of the
integration (PLAN #156).

## Related

- [Entities](../reference/entities.md) — what the quarter-hour sensor reports and why it writes states.
- [Data model](../explanation/data-model.md) — the 1–3 day publication lag behind every value.
- [Live values next to the archive](live-and-archive.md) — pairing the archive with a local live sensor.
- [ADR-0009](../explanation/decisions/0009-resolution-and-storage.md) — hourly statistics, quarter-hour
  states, and why no sub-hourly history can be imported.
