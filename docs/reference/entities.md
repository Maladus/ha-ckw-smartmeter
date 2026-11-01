# Entities

This page answers: which entities and statistics does the integration create for a meter, with which
unit, device class, unique id and update cadence, and which of them can feed the Energy dashboard?

Lookup only. The integration creates one device per delivery point (Zählpunkt) and seven sensors on
it; four are enabled, three are opt-in details. The device hangs under a service device named
**CKW Smart Meter** (model `OpenEnergy API`) that belongs to the config entry, and is itself named
after the meter model plus the masked Zählpunkt, for example `SMART *****************************1234`.

## Identity

Every sensor has one device, one unique id and one statistic:

| Property | Value |
| --- | --- |
| Unique id | `{entry_id}_{deliverypointnumber}_{key}`, where `key` is the value in the `translation_key` column below. Append-only, never recomputed. |
| Entity id | `sensor.<device slug>_<entity name slug>`, e.g. `sensor.smart_1234_consumption_total` for the meter in the name above. |
| Statistic id | The entity id. Statistics are imported with `source: recorder`, `has_sum: true`, one row per local day in `Europe/Zurich`, hourly resolution. |
| Device | `identifiers: {(ckw, <entry_id>_<masked zählpunkt>)}`, `via_device: (ckw, <entry_id>)`. |

The unique id is append-only: no rename, no reuse of a retired id, and no re-derivation after a
reload, because the statistic id hangs off it and a changed id splits the history into two series. A
rename of an id needs an ADR.

## Consumption meter

For a meter with `type: consumption` or `type: import` — the ordinary household meter.

| Entity name | `translation_key` | Unique id | Unit | `device_class` | `state_class` | `last_reset` | Statistic id | Enabled by default | Updates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Consumption total | `consumption_total` | `{entry_id}_{zählpunkt}_consumption_total` | kWh | `energy` | `total_increasing` | never | the entity id | yes | every poll |
| Consumption yesterday | `consumption_yesterday` | `{entry_id}_{zählpunkt}_consumption_yesterday` | kWh | `energy` | `total` | start of the reported local day | the entity id | yes | every poll |
| Last update | `last_update` | `{entry_id}_{zählpunkt}_last_update` | — (timestamp) | `timestamp` | — | — | the entity id | yes | every poll |
| Last published interval | `last_published_interval` | `{entry_id}_{zählpunkt}_last_published_interval` | — (timestamp) | `timestamp` | — | — | the entity id | yes | every poll |
| Consumption high tariff | `consumption_high_tariff` | `{entry_id}_{zählpunkt}_consumption_high_tariff` | kWh | `energy` | `total_increasing` | never | the entity id | no | every poll |
| Consumption low tariff | `consumption_low_tariff` | `{entry_id}_{zählpunkt}_consumption_low_tariff` | kWh | `energy` | `total_increasing` | never | the entity id | no | every poll |
| Quarter-hour consumption | `quarter_hour` | `{entry_id}_{zählpunkt}_quarter_hour` | kWh | `energy` | `measurement` | never | the entity id | no | every poll, current local day only |

What the numbers are:

- **Consumption total** sums the billed kWh of every interval the integration has seen for this meter,
  including the 730-day backfill ([data model](../explanation/data-model.md)).
- **Consumption yesterday** is the kWh of the newest *complete* day CKW has published, which is 1–3
  days old. Its `last_reset` is the local midnight that day starts at, so the value is a daily
  quantity and not a running sum.
- **Consumption high tariff** and **Consumption low tariff** are the HT/NT split of the same total.
  Their sum equals **Consumption total**.
- **Quarter-hour consumption** is the newest complete 15-minute interval of the current local day,
  and has no value when that interval is incomplete or quality-flagged. It writes entity states,
  never statistics, so its history is bounded by `recorder.purge_keep_days` (10 days by default) —
  [quarter-hour history](../explanation/quarter-hour-history.md).
- **Last update** is when the coordinator's last successful poll happened; **Last published interval**
  is the end of the newest day CKW has published. Both are `entity_category: diagnostic`.

## Feed-in meter

For a meter with `type: production` or `type: export` — PV feed-in or another export. The entity set is
identical: same names, same translation keys, same unique-id shape, same units, classes and cadence.
Only the interpretation changes, so the tables below repeat the columns and state what the number is
on a feed-in meter.

| Entity name | `translation_key` | Unique id | Unit | `device_class` | `state_class` | `last_reset` | Statistic id | Enabled by default | Updates | The number is |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Consumption total | `consumption_total` | `{entry_id}_{zählpunkt}_consumption_total` | kWh | `energy` | `total_increasing` | never | the entity id | yes | every poll | cumulative kWh fed into the grid |
| Consumption yesterday | `consumption_yesterday` | `{entry_id}_{zählpunkt}_consumption_yesterday` | kWh | `energy` | `total` | start of the reported local day | the entity id | yes | every poll | kWh fed in on the newest complete published day |
| Last update | `last_update` | `{entry_id}_{zählpunkt}_last_update` | — (timestamp) | `timestamp` | — | — | the entity id | yes | every poll | when the coordinator's last successful poll happened |
| Last published interval | `last_published_interval` | `{entry_id}_{zählpunkt}_last_published_interval` | — (timestamp) | `timestamp` | — | — | the entity id | yes | every poll | end of the newest day CKW has published |
| Consumption high tariff | `consumption_high_tariff` | `{entry_id}_{zählpunkt}_consumption_high_tariff` | kWh | `energy` | `total_increasing` | never | the entity id | no | every poll | HT part of the fed-in quantity |
| Consumption low tariff | `consumption_low_tariff` | `{entry_id}_{zählpunkt}_consumption_low_tariff` | kWh | `energy` | `total_increasing` | never | the entity id | no | every poll | NT part of the fed-in quantity |
| Quarter-hour consumption | `quarter_hour` | `{entry_id}_{zählpunkt}_quarter_hour` | kWh | `energy` | `measurement` | never | the entity id | no | every poll, current local day only | newest complete 15-minute fed-in interval |

The names keep the word "consumption" on a feed-in meter, because one entity description serves both
meter types. There is no feed-in tariff calculation: the CHF fields of the API are parsed but expose
no entity ([data model](../explanation/data-model.md)).

## Not supported

| Meter | What happens |
| --- | --- |
| Gas meter (`fuel: gas`) | No entities. The sensor platform skips any meter whose fuel is not `elec`. |
| Meter the API does not list | No entities. The 33-character Zählpunkt from `/smartmeter/customer` is the only key; it is never guessed. |
| Legacy electromechanical meter | No entities. No daily aggregate exists to fetch ([data model](../explanation/data-model.md)). |

## Attributes

Only two sensors carry extra attributes; every other sensor has none.

| Entity | Attributes |
| --- | --- |
| Consumption total | `publication_delay_h` — hours between the newest published interval and the poll; `last_published_interval` — the same timestamp the diagnostic sensor shows; `quality_intervals` — how many intervals were flagged by `anzahl_linien_* > 1` and therefore kept out of the totals; `stale_since` — see below |
| Quarter-hour consumption | `interval_start`, `interval_end` — UTC bounds of the reported 15-minute interval; `publication_delay_h` — hours between `interval_end` and the poll |

`stale_since` appears on **Consumption total** once its meter is stale. It is the timestamp of the first
poll that no longer returned the delivery point, and it disappears as soon as CKW reports the meter again.
Home Assistant drops the attributes of an unavailable entity, so while the meter is stale a user sees the
unavailable state and the one `INFO` line in the log. The attribute itself stays on the entity, which is
where a diagnostic dump or a template on the entity object reads it.

`publication_delay_h` above 96 h logs a warning and the newest interval standing still for more than
4 days logs a stall warning, both once per failure class.

## Energy dashboard

The Energy dashboard reads long-term statistics, so it lists every sensor with `device_class: energy`
and a sum. Which of them to add:

| Entity | In the dashboard picker | Use it there |
| --- | --- | --- |
| Consumption total | yes | **yes** — add it as grid consumption (or as return to grid on a feed-in meter). It is the cumulative billed series. |
| Consumption high tariff | yes | no — it is a subset of the total, so adding it beside the total double-counts every kWh. |
| Consumption low tariff | yes | no — same reason. |
| Consumption yesterday | yes | no — a single daily value with a `last_reset`, not a cumulative source. |
| Quarter-hour consumption | no | no — `state_class: measurement` has no sum, and the dashboard never queries finer than an hour. |
| Last update, Last published interval | no | no — diagnostics, not energy. |

## Diagnostics

- `last_update` and `last_published_interval` are `entity_category: diagnostic` and appear in the
  device's diagnostic section rather than among its controls.
- The `publication_delay_h` and `quality_intervals` attributes of **Consumption total** are the
  numbers to read when the newest value looks stale or the total looks low.
- Both diagnostic entities are timestamps, so they read as a date and time, never as a state string.

## Availability and cadence

| Situation | Behaviour |
| --- | --- |
| Normal poll | All sensors of the meter update together; the coordinator owns the only network call. |
| Poll interval | 2 h by default, selectable 1, 2, 4, 6 or 12 h in the integration's options. The entities follow the coordinator, they do not poll themselves. |
| Failed poll | The sensors of the affected meter become unavailable until a poll succeeds; they are never removed. |
| Delivery point added in the CKW portal | Its seven sensors and its device appear on the poll that first returns the meter, without a reload. The unique ids and the entity ids follow the same scheme as for every other meter. |
| Meter missing from the poll's data | The meter's sensors become unavailable immediately, and its entities, its history and its device stay registered. A meter CKW lists but never publishes data for behaves the same way. |
| Meter missing for more than 7 days (`STALE_AFTER_DAYS`) | The meter is **stale**. One `INFO` line in the log, `stale_since` on Consumption total, and no repair issue, because CKW can list the meter again. When it does, one `INFO` line, the sensors are available again and `stale_since` is gone. The clock runs from the first poll that did not return the meter, so a meter the portal still lists without data is covered too. |
| Stale device deleted by the user | Allowed while the meter is absent, and the decision sticks: the device is not registered again on later polls, and its statistics rows stay in the recorder under the entity ids (the series continues if the same meter comes back). |
| Meter exchange | A changed `supply_start_date` means a new physical meter. The cumulative series cannot continue across two meters: the integration raises a repair issue (#133) naming the change, and the new meter is imported under a new statistic id while the old series stays untouched as history. Re-import goes through [backfill history](../how-to/backfill-history.md). |

## Related

- [Data model](../explanation/data-model.md) — why the values are 1–3 days late, what HT/NT and the
  quality flags mean.
- [Quarter-hour history](../explanation/quarter-hour-history.md) — why 15-minute values are states
  and never statistics.
- [Conventions](conventions.md) — the append-only unique-id rule and the statistic-id consequence.
- [Getting started](../tutorial/getting-started.md) — the install path that creates these entities.
- [Backfill history](../how-to/backfill-history.md) — re-importing a statistic after a meter exchange
  or a recorder wipe.
- [Live values next to the archive](../how-to/live-and-archive.md) — the local live sensor that pairs
  with these archive entities.
- [Documentation map](../README.md) — where this page sits among the quadrants.
