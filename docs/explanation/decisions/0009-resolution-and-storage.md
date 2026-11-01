# 0009 — Keep statistics hourly and quarter-hour history in states

- Status: accepted
- Date: 2026-11-01
- Deciders: Martin Wigger

## Context

CKW serves quarter-hour rows: the deployed API model only ever shows 15-minute measurement
examples, and `minute`/`minutes` is the finest aggregation the endpoints offer. Home Assistant
2026.10 can store exactly two resolutions. The recorder writes 5-minute short-term statistics —
by default for 10 days — and hourly long-term statistics. The import path
(`async_import_statistics`, or the `recorder/import_statistics` websocket command behind it)
rejects any row whose timestamp is not on the full hour; the refusal is the whole batch, and
nothing is written. Everything else the recorder purges after `recorder.purge_keep_days`, which
defaults to 10 days.

Both facts were measured, not read: see [quarter-hour history](../quarter-hour-history.md) for
the container, the payloads and the error text.

Two more measurements shape the decision. The Energy dashboard never asks for a resolution finer
than an hour: its graphs query `hour` for a single day, `day` up to a month and `month` beyond
that, and only the *power* view switches to `5minute` — which reads the recorder-written
short-term table and needs a power sensor. And an imported statistic is routed by the shape of
its statistic id: `source="recorder"` with `statistic_id=<entity_id>` keeps it selectable in the
Energy-dashboard picker, an external `ckw:…` statistic is not.

The integration's data is daily: one billed aggregate per local day and meter, arriving 1 to 3
days late ([data model](../data-model.md), [ADR-0004](0004-poll-interval-and-data-lag.md)).
Whatever is decided here has to fit that cadence, not fight it.

## Decision

Three parts, one per question a user can ask about resolution and retention.

1. **The Energy dashboard is fed by hourly long-term statistics, imported from the daily
   aggregates.** `async_import_statistics` with `source="recorder"`,
   `statistic_id=<entity_id>`, `has_sum=True`, one row per local day
   ([ADR-0002](0002-statistics-strategy.md)). Hourly is what the recorder accepts and what the
   dashboard reads; the day boundaries stay ours, in `Europe/Zurich`.

2. **The 15-minute series is a quarter-hour sensor whose history lives in recorder `states`.**
   `sensor.ckw_<meter>_quarter_hour` is fetched per poll for the current day, carries
   `interval_start`/`interval_end`/`publication_delay_h` as attributes, and writes states, not
   statistics. Its history is bounded by `recorder.purge_keep_days` — 10 days by default. The
   integration **never** writes a statistic below one hour, in either direction: no 15-minute
   import, and no pretending that the hourly rows are finer than they are.

3. **Retroactive 15-minute history is not possible in Home Assistant.** The only path to
   quarter-hour values from before the installation day is an external store, and the supported
   one is InfluxDB fed by a backfill script (`scripts/ckw-influx-backfill.py`, #156). InfluxDB is
   an exporter of the sensor's states, never a statistics backend for this integration.

## Consequences

**Positive**

- Every number the Energy dashboard shows comes from a supported path: an entity statistic the
  dashboard can select, imported at the resolution the recorder validates. No direct SQLite
  writes, no rows the read path would stretch to an hour and mis-weight fourfold.
- The quarter-hour sensor costs one row per 15 minutes only while the recorder keeps it, so the
  default database growth stays small and the retention is a user setting, not a hidden constant.
- Long retention is possible without the integration owning storage: InfluxDB keeps exporting
  what the recorder would purge.

**Negative**

- **History starts at installation.** The quarter-hour series has no past, and no repair run
  brings it back; only a local live source or the InfluxDB backfill produces anything older.
- **A reinstall loses that history** unless it was exported first: new config entry, new entity
  ids, and the recorder keeps states by entity id, not by meter.
- The 15-minute series and the daily statistics will disagree, because one is an instantaneous
  quarter-hour value and the other a billed daily aggregate; nothing here reconciles them.
- Anything older than `purge_keep_days` is gone from Home Assistant. The supported answer is an
  external store, which is a second system to run.

**Follow-ups**

- The integration logs the **effective** retention at startup and raises a low-severity repair
  issue when `purge_keep_days` is below 30 days (#157), so a user who wants quarter-hour history
  learns the limit before it deletes something.
- The docs state all of this in user language: `data-model.md` (#125), the live-vs-archive how-to
  (#155) and the retention guidance in the how-to pages (#157).
- If the hour rule is ever lifted, this ADR is superseded — not edited. Re-run the measurement in
  [quarter-hour history](../quarter-hour-history.md) first; a container start and three websocket
  calls settle it.

## Alternatives considered

**Import the 15-minute rows as long-term statistics.** The natural first design, and it fails on
the first row: the recorder rejects any timestamp that is not on the hour, with
`Invalid timestamp: timestamps must be from the top of the hour`. Measured, including the
`source` variations and the "no half-imported series" check. Rejected by the platform, not by
preference.

**Use sub-hourly "short-term" statistics instead.** The 5-minute table exists and a
`period: "5minute"` query works, but it is written by the recorder from live entity states, not
importable: an import lands in the hourly table only, and a `5minute` query for an imported
statistic returns zero rows. There is no import target below an hour at any setting.

**Write quarter-hour rows into the statistics table directly at the SQLite level.** Possible, and
harmful: the read path returns such a row unchanged and labels it `end = start + 1 hour`, so a
15-minute value would be charted as an hour-long bucket with four times its real weight. Worse
than no history, because it looks plausible. Rejected as unsupported and misleading.

**Tell every user to raise `recorder.purge_keep_days` globally as the default advice.** The setting
is global: raising it to a year keeps *every* entity's states for a year, not just the quarter-hour
sensor, so the database grows with the whole instance. That is a real cost the integration cannot
pay on the user's behalf. The how-to therefore offers it as a choice with size estimates, and logs
the effective value instead of silently changing the user's recorder configuration.

**Let the integration own a store (a file, a small database, an attribute cache).** It would survive
`purge_keep_days`, and it breaks everything else: backup and restore no longer carry the history
(the user's Home Assistant backup would silently miss it), the data is invisible to the history
card and to `history/history_during_period`, and a second storage system inside an integration is
not idiomatic Home Assistant. Rejected; the recorder plus an external exporter is the supported
shape.

**Expose the quarter-hour series as sensor attributes only.** Attributes are overwritten by the
next state and are not part of the recorded state history in a queryable way, so there would be no
15-minute series at all — only the latest interval. Rejected: it answers "what was the last quarter
hour" and nothing else.

## Links

- [Quarter-hour history in Home Assistant](../quarter-hour-history.md) — the measurement this ADR
  rests on: the rejection, the control import, the query behaviour and the Energy-dashboard source.
- [Data model](../data-model.md) — the daily cadence, the 1–3 day lag and the known limitations.
- [ADR-0002](0002-statistics-strategy.md) — the entity-statistics import this ADR keeps.
- [ADR-0004](0004-poll-interval-and-data-lag.md) — the poll and the rolling window that fill both
  the statistics and the quarter-hour states.
- Issues #152 (quarter-hour sensor), #153 (this ADR), #155 (live-vs-archive docs), #156 (InfluxDB
  backfill), #157 (retention logging and repair issue), #124 (entities reference).
