# Backfill the history

This page answers: the daily history has a gap, starts too late or vanished with the recorder — how do
I re-import it from CKW?

Call the **`ckw.backfill`** action. It re-imports the daily aggregates of one account as long-term
statistics, in chunks, paced against CKW's rate limit and resumable after an interruption. It
overwrites rows instead of adding to them, so running it twice does not double a total.

## When to use it

| Situation | What it repairs |
| --- | --- |
| A meter was exchanged | The repair issue **CKW meter exchanged for `<delivery point>`** names the new supply start date; re-importing rebuilds the new meter's series from CKW ([entities](../reference/entities.md)) |
| The recorder database was wiped, restored from an old backup or recreated | The statistics rows are gone with it; the import writes them again |
| A long outage, a reinstalled Home Assistant or a Home Assistant version that dropped the custom integration | The days that were never imported |
| The 730-day import never finished | The remaining chunks, from the stored offset |
| You deleted a statistic by mistake in **Developer Tools → Statistics** | The rebuilt cumulative sum for that statistic id |
| **Consumption total** looks too low after a repair | The whole series, recomputed from CKW |

Do **not** start with a backfill when the values are merely *late* or a login is broken: a rejected
sign-in stops the job on its first chunk. Use [reauthenticate](reauthenticate.md) first, then come
back.

## Run it

1. Open **Developer Tools → Actions**.
2. Search for **`ckw.backfill`**, listed as **Backfill statistics**.
3. Fill in the fields, or paste the YAML below into the action's YAML mode.

| Field | UI name | Allowed | Default | Meaning |
| --- | --- | --- | --- | --- |
| `entry_id` | Config entry | an existing CKW config entry id | empty — every loaded CKW entry | Which account to import. An id that belongs to no entry fails with "No CKW account has the id <entry_id>." |
| `days` | Days | 1–3650 | `730` | How many days to import, ending yesterday |
| `force` | Force | `true`, `false` | `false` | Recompute the cumulative sum from the first imported day and ignore the stored checksum |

A 730-day import for one entry, the ordinary repair:

```yaml
action: ckw.backfill
data:
  days: 730
```

You should now see the call stay open for as long as the job runs — a full 730-day import takes a few
minutes, because the chunks are deliberately spaced — and then return. A failure returns as an error
message in the same dialog instead of a silent success.

## What it actually does

- It walks the window from its oldest day towards yesterday, so the cumulative sum grows forward.
  `days: 730` therefore ends yesterday and starts 729 days earlier.
- One request covers **30 days**, then the job pauses **one second**, which keeps the whole import
  below CKW's quota.
- The rows are hourly long-term statistics with `source: recorder` and the entity id as statistic id,
  one row per local day in `Europe/Zurich` ([ADR-0002](../explanation/decisions/0002-statistics-strategy.md)).
  Nothing finer than an hour can be imported at all.
- Re-importing a window that overlaps what the recorder already holds **walks back from the stored
  anchor**, so the days imported before keep exactly the sums they had. Asking for a *longer* window
  than before (say `days: 3650` after a 730-day job) starts earlier and rebuilds the sum forward; the
  log says `statistics: importing history older than the stored sum … the cumulative sum restarts at
  the first re-imported day`.
- `force: true` drops the stored sum and recomputes from the **first day of the window you asked
  for**. Use it with the full window you want rebuilt — `force: true` with `days: 30` rebuilds only
  those 30 days and restarts the cumulative series at the oldest of them.
- Two jobs for the same entry do not run side by side: the second logs `backfill: one is already
  running for <title>, not starting a second` and returns.

## Watch the progress

1. **The log**, one line per chunk and one at the end:

   ```text
   backfill: importing 730 day(s) of statistics <start>..<end> for <title> (25 chunk(s) of 30 days)
   backfill: <start>..<end> done, 1/25
   …
   backfill: finished 25/25 chunk(s) for <title>, <rows> row(s) imported
   ```

   A chunk that fails stops the job and keeps its place:

   ```text
   backfill: stopped at <date>, <n>/<total> chunk(s) done; the offset is kept
   ```

   Fix the cause — sign-in, `429`, connection — and call the action again; it continues from that day.

2. **The runtime state**, `entry.runtime_data.statistics_state`: `done`, `running`, `days`,
   `next_start`, `chunks_done` and `chunks_total`. It is what the entry's diagnostics download prints
   under `statistics`, so **Settings → Devices & services → CKW Smart Meter → three-dot menu →
   Download diagnostics** is how you read it without a terminal. `done: true` means the last job
   finished; `next_start` is the first day still owed.

3. **The offset file**, `.storage/ckw.statistics.<entry_id>`. It holds the chunk offset of the
   backfill and the sum anchor of every delivery point, and it is bookkeeping only: the history itself
   is in the recorder. Never edit it by hand — delete it and the next import restarts the sum from
   scratch, which is not the same as a clean repair.

The rows you asked for are what the recorder holds afterwards. Check them in **Developer Tools →
Statistics** under the meter's **Consumption total** statistic, for example
`sensor.smart_1234_consumption_total`: one row per imported day, with the cumulative sum.

## What it cannot repair

- **15-minute history from before the installation.** Home Assistant has no import path below one
  hour, so the quarter-hour series starts on the day the sensor was enabled and never reaches
  backwards ([ADR-0009](../explanation/decisions/0009-resolution-and-storage.md),
  [quarter-hour history](../explanation/quarter-hour-history.md)). If you want that detail, the ways
  are a local reader and an external store — [keep the quarter-hour history](keep-quarter-hour-history.md).
- **A statistic id that changed.** The old history stays under the old id; the new series is a new
  statistic, and nothing merges the two. That is exactly what a meter exchange does: the old meter
  keeps its series, the new one starts its own. A backfill rebuilds each of them from CKW, it does not
  join them ([entities](../reference/entities.md)).
- **A day CKW never published, or that CKW flags** with more than one timeseries
  (`anzahl_linien_* > 1`). Such an interval is skipped rather than imported as a guess, so the gap
  stays a gap and the repair issue **CKW flags data quality** names its cause at CKW.
- **Anything older than what CKW still serves.** The API's history depth is not documented; the
  integration asks for what you name and imports what comes back.

## On the dev instance

The import and the backfill only behave deterministically on an empty recorder database:

1. `mise run dev-reset` removes the container, deletes `config/home-assistant_v2.db*` and starts Home
   Assistant again with onboarding kept.
2. If you stopped the instance first, `mise run dev-up` starts it and waits for it to answer.
3. `mise run dev-smoke` proves the component loads without a traceback; then call `ckw.backfill` from
   the dev instance's Developer Tools exactly as above.

A full wipe including `.storage` and the onboarding token is `mise run dev-reset -- --full`
([dev instance runbook](../../scripts/dev/README.md#tests-that-need-a-clean-database)).

## Related

- [Reauthenticate](reauthenticate.md) — clearing a rejected sign-in, which a backfill cannot get past.
- [Debug a connection](debug-connection.md) — `429` during a backfill, an interrupted import, missing
  Energy-dashboard statistics and the log levels.
- [Configuration](../reference/configuration.md#action-ckwbackfill) — the action's fields as declared
  in `services.yaml`.
- [Entities](../reference/entities.md) — the statistic ids, the meter-exchange behaviour and the
  Energy-dashboard eligibility.
- [Install](install.md#delete-the-leftover-statistics-and-entities) — deleting the leftover statistics
  and entities, the other half of a clean restart.
- [Quarter-hour history](../explanation/quarter-hour-history.md) — the measurement behind "nothing
  below one hour", and what an external store can hold instead.
- [Tutorial: getting started](../tutorial/getting-started.md) — the automatic 730-day import this page
  re-runs by hand.
