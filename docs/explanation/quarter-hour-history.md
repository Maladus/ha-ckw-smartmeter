# Quarter-hour history in Home Assistant

This page answers: can 15-minute history be backfilled into Home Assistant, and how does it show up?

The answer decides whether the integration can import CKW's quarter-hour series
(`aggregation=minute|minutes`) as long-term statistics — the option PLAN #152 and the ADR-0009 draft
(#153) had left open. Everything below was measured on 2026-11-01 against a throwaway
`ghcr.io/home-assistant/home-assistant:stable` container (`homeassistant` **2026.10.4**, frontend
`20260930.5`, SQLite recorder), onboarded through the API, with a long-lived token and the recorder's
websocket commands. No CKW endpoint was touched.

## Verdict

**(c) Rejected.** Home Assistant 2026.10 refuses any imported statistics row whose timestamp is not on
the hour, and the refusal is the whole batch:

```text
recorder/import_statistics  statistic_id=ckw:spike_qh    source=recorder  200 rows, 15 min
→ error home_assistant_error: Invalid source

recorder/import_statistics  statistic_id=ckw:spike_qh    source=ckw       200 rows, 15 min
→ error home_assistant_error: Invalid timestamp: timestamps must be from the top of the hour (minutes and seconds = 0)

recorder/import_statistics  statistic_id=sensor.spike_qh source=recorder  200 rows, 15 min
→ error home_assistant_error: Invalid timestamp: timestamps must be from the top of the hour (minutes and seconds = 0)
```

A rejected batch leaves nothing behind: `statistics_meta` gained no row for either statistic id, so
there is no half-imported series to repair. The same series aggregated to whole hours imports and is
stored as sent.

Three consequences, in order of how much they change the plan:

1. The 15-minute backfill design (`source: recorder`, chunked per month, paced, resumable) cannot be
   built. It fails on the first row of the first chunk, before anything is written.
2. There is no sub-hour resolution to fall back to. 5-minute statistics exist as a table
   (`statistics_short_term`) and as a `period: "5minute"` query, but they are written by the recorder
   from live entity states; imports land in the hourly `statistics` table only, and a `5minute`
   query for an imported statistic returns no rows.
3. Writing quarter-hour rows into the table directly is possible at the SQLite level and harmful:
   the read path returns each row unchanged and labels it `end = start + 1 hour`, so a quarter-hour
   value would be displayed as a one-hour bucket.

## What was measured

### The import path is not a service

`recorder.import_statistics` is **not a service** in 2026.10. `get_services` lists five recorder
services, and `POST /api/services/recorder/import_statistics` answers `400` with the body
`400: Bad Request`:

```text
recorder services: ['disable', 'enable', 'get_statistics', 'purge', 'purge_entities']
```

The import path is the websocket command `recorder/import_statistics` (what the frontend's developer
tools use) or `async_import_statistics()` from inside an integration. Both reach the same validation.

### The websocket shape differs from older examples

```json
{"type": "recorder/import_statistics",
 "metadata": {"statistic_id": "...", "source": "...", "name": "...",
              "unit_of_measurement": "kWh", "has_mean": false, "has_sum": true,
              "mean_type": 0, "unit_class": "energy"},
 "stats": [{"start": "2026-09-01T00:00:00+02:00", "state": 0.1, "sum": 0.1}]}
```

The flat form (`statistic_id` at the top level) is rejected with
`required key not provided at 'metadata'. Got None`. Routing is by the *shape of the statistic id*, not
by `source`:

| `statistic_id` | Routed to | Required `source` | Measured error when wrong |
| --- | --- | --- | --- |
| `sensor.spike_qh` (valid entity id) | `async_import_statistics` | `recorder` | — |
| `ckw:spike_qh` (valid statistic id) | `async_add_external_statistics` | `ckw` (the part before `:`) | `Invalid source` |

So the natural first attempt — an external id written as `ckw:spike_qh` with `source: recorder` — is
a shape error, not a resolution error. It is worth separating, because the two cases have different
consequences later: `source: recorder` keeps the statistic tied to an entity (Energy-dashboard picker,
ADR-0002), while an external statistic is not selectable there.

`mean_type` and `unit_class` are required in practice from 2026.11. Omitting them still imports, but
logs:

```text
WARNING WS command recorder/import_statistics called without specifying mean_type in metadata,
        this is deprecated and will stop working in HA Core 2026.11
WARNING WS command recorder/import_statistics called without specifying unit_class in metadata,
        this is deprecated and will stop working in HA Core 2026.11
```

The check that stops quarter-hour rows lives in `homeassistant/components/recorder/statistics.py`,
`_async_import_statistics()`, before the row is queued:

```python
if start.minute != 0 or start.second != 0 or start.microsecond != 0:
    raise HomeAssistantError(
        "Invalid timestamp: timestamps must be from the"
        " top of the hour (minutes and seconds = 0)"
    )
```

### The control: hourly rows are accepted and stored as sent

Same payload, same window, quarter-hour values summed into 50 whole hours and the cumulative `sum`
recomputed:

```text
recorder/import_statistics  statistic_id=ckw:spike_hourly  source=ckw  50 rows, 1 h
→ success: true

sqlite> select count(*), min(start_ts), max(start_ts) from statistics
        where metadata_id=(select id from statistics_meta where statistic_id='ckw:spike_hourly');
50|1788213600.0|1788390000.0        -- 49 h apart, i.e. 3600 s per row
```

`min(start_ts)` is `2026-08-31T22:00:00Z`, the UTC instant of `2026-09-01T00:00+02:00`. The spacing is
the stored spacing — nothing is snapped, resampled or reordered on write.

### What the query path returns

`recorder.get_statistics` over REST (`?return_response`), window `2026-09-01T00:00+02:00` to
`06:00+02:00`, `types: [change, sum, state]`:

| `period` | Rows returned | What came back |
| --- | --- | --- |
| `5minute` | 0 | nothing: the short-term table is empty and is not filled by imports |
| `hour` | 6 | the six stored hourly rows, `sum` cumulative as imported |
| `day` | 2 | resampled server-side into two UTC days (`2026-08-31`, `2026-09-01`) |

Two details matter for the integration. `5minute` reads a different table than `hour` and `day`, so
"just query finer" does not help. And the day buckets are **UTC** days in the recorder, which is why
[conventions](../reference/conventions.md) requires the integration to emit one bucket per *local*
day instead of letting the recorder bucket.

### The direct-SQLite side experiment (not a supported path)

To separate "the schema cannot hold it" from "the API refuses it", eight quarter-hour rows were
inserted straight into the `statistics` table with their own `statistics_meta` row
(`mean_type` is `NOT NULL`, everything else tolerates the finer `start_ts`):

```text
8 rows at 900 s spacing written, read back unchanged:
(1791151200.0, 0.25, 0.25) (1791152100.0, 0.25, 0.5) ... (1791157500.0, 0.25, 2.0)

period=5minute → 0 rows
period=hour    → 8 rows, each returned as {'start': '...T22:00:00+00:00',
                                           'end':   '...T23:00:00+00:00',
                                           'state': 0.25, 'sum': 0.25}
period=day     → 1 row, change 2.0
```

The schema stores the rows; the read path then stretches each one to an hour. A 15-minute value would
appear in a chart as an hour-long bucket with four times its real weight — worse than missing history,
because it looks plausible.

### How the Energy dashboard treats it

Confirmed, with one exception. The shipped frontend (`hass_frontend-20260930.5`) picks the query
period from the selected range, not from what is stored. Verbatim from the minified energy bundle,
where `r` is the range in days and `o` selects the power ("live") view:

```js
function Mt(t,e,o=!1){const r=(0,n.c)(e||new Date,t);
  return o?r>64?"day":r>8?"hour":"5minute":
    (0,s.e)(t)&&(!e||(0,a.c)(e))&&r>35?"month":r>2?"day":"hour"}
```

Energy graphs therefore ask for `hour` for a single day, `day` for up to a month and `month` beyond
that — never `5minute`. The exception is the power view, which switches to `5minute` for ranges of
eight hours or less; that view reads `statistics_short_term` and needs a power sensor, neither of
which an import can supply.

So hourly is the finest resolution the dashboard ever reads for energy, and a daily-only backfill
(one row per day, ADR-0002) is already shown at hourly granularity by resampling. The dashboard does
not care that quarter-hour data exists.

## Consequences for the backfill design

- **Backfill stays daily.** One statistics row per local day, ~730 days, `source: recorder`, hourly
  aggregation left to the read path (ADR-0002, [backfill history](../how-to/backfill-history.md)). No
  chunked-per-month 15-minute import, no resumable quarter-hour job: there is nothing to resume.
- **Quarter-hour data is live-forward only.** The `sensor.ckw_<meter>_quarter_hour` entity (#152)
  writes states, not statistics, so its history starts at installation day and ends at
  `purge_keep_days`. This is the same conclusion the plan reached; the experiment removes the
  "maybe we can import it after all" caveat rather than adding one.
- **Long retention for 15-minute values means InfluxDB** (or another external store), as PLAN #153
  already says and #156 now plans in detail: Home Assistant's own long-term statistics cannot hold
  them at any price, and the recorder purges the states.
- **Metadata must be complete.** `mean_type: 0` (`NONE`) and `unit_class: "energy"` on every import,
  because 2026.11 makes their absence an error. `unit_of_measurement` must be in the energy
  converter's valid units (`kWh` is).
- **The measured cost is small and finite.** A user gets no retroactive quarter-hour history, but the
  Energy dashboard would not have displayed it — it would have been stored for InfluxDB only. That is
  one more reason to keep the API requests at `days` for the backfill.

## Open questions

- **Will the hour rule ever be lifted?** It is a hard validation in the import path, and the 5-minute
  table is a recorder-internal rolling window rather than an import target. Re-run the experiment
  above before assuming anything changed — a container start plus three websocket calls settle it.
- **What can a state-only quarter-hour sensor drive?** Its states are queryable through
  `history/history_during_period` and the history graph card, but not through the Energy dashboard's
  energy graphs. Whether that is enough for the intended use (quarter-hour detail for automations and
  `statistics`-free charts) is a product question, not a recorder question.
- **Does the power view accept a 15-minute state series?** Untested here: it needs a real entity with
  `device_class: power` and `state_class: measurement` plus the `5minute` short-term rows the recorder
  derives from its states. That is exactly what a later live-source experiment would measure.
- **Does the direct-SQLite path survive a recorder restart and a repack?** Not tested; it is recorded
  only to prove where the refusal lives, and it is not an implementation option for an integration.

## Reproducing

```bash
mkdir -p /tmp/ha-spike
podman run -d --name ha-spike -p 8124:8123 -v /tmp/ha-spike:/config:Z \
  -e TZ=Europe/Zurich ghcr.io/home-assistant/home-assistant:stable

# onboarding: POST /api/onboarding/users → auth_code → /auth/token → access token
curl -s -X POST localhost:8124/api/onboarding/users -H 'Content-Type: application/json' \
  -d '{"client_id":"http://localhost:8124/","name":"Spike","username":"spike",
       "password":"…","language":"en"}'
# then core_config, analytics and integration, each with the bearer token
# (integration returns a second auth_code; exchange it at /auth/token to finish)

# a long-lived token over the websocket, then the import with 15-minute rows
#   {"type":"recorder/import_statistics","metadata":{…},"stats":[…]}
# and the read side:
curl -s -X POST 'localhost:8124/api/services/recorder/get_statistics?return_response' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"start_time":"2026-09-01T00:00:00+02:00","end_time":"2026-09-01T06:00:00+02:00",
       "statistic_ids":["ckw:spike_hourly"],"period":"5minute",
       "types":["change","sum","state"]}'

# the stored rows (the image has no sqlite3 binary, use python3 inside the container)
podman exec ha-spike python3 -c "
import sqlite3; c=sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro', uri=True)
print(list(c.execute('select count(*), min(start_ts), max(start_ts) from statistics')))"

# where the refusal lives
podman exec ha-spike sh -c 'grep -n "top of the hour" -B4 \
  /usr/src/homeassistant/homeassistant/components/recorder/statistics.py'

podman rm -f ha-spike && rm -rf /tmp/ha-spike
```

## Related

- [data-model.md](data-model.md) — why the integration's numbers stop at yesterday and what the
  quarter-hour sensor is for.
- [ADR-0002](decisions/0002-statistics-strategy.md) — the daily entity-statistics import this
  experiment leaves standing.
- ADR-0009 (planned, #153) — resolution and storage; this page is the measurement behind its "no
  15-minute statistics import" clause, and the ADR index is
  [decisions/README.md](decisions/README.md).
- [ckw-openenergy-api-verification.md](../reference/ckw-openenergy-api-verification.md) — the
  quarter-hour rows the API serves, and how far that is from what can be stored.
- [ckw-openenergy-api.md](../reference/ckw-openenergy-api.md) — the `minute`/`minutes` aggregation
  and the `days` aggregation the backfill uses.
- [PLAN.md](../../PLAN.md) — rows #152, #153, #154 and the "15-minute resolution" decision.
