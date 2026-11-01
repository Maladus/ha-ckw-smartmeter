# Data model

This page answers: why the numbers arrive 1 to 3 days late, what HT/NT and the CHF amounts actually
mean, why some intervals are flagged instead of summed, and which meters this integration can serve.

The integration does not invent a data model — it inherits CKW's. Every field it exposes comes from
the two GET endpoints documented in the [API reference](../reference/ckw-openenergy-api.md); where
that contract is silent, this page says so instead of guessing.

## Data arrives 1 to 3 days late

The contract says the OpenEnergy API serves data up to yesterday, never today: one value per delivery
point per closed day at the `days` aggregation, because the meter may legally be read only once per
day. In practice, the newest interval is **1 to 3 days old** — the delay varies by meter and by day,
and "up to yesterday" is a ceiling, not the observed arrival time. CKW's own sample data is
quarter-hourly — every measurement example in the deployed model is 15 minutes wide, no 1-minute
example exists, and the public price feed returns 96 quarter-hour slots per day. `minute` and
`minutes` are therefore the finest *available* resolution, not one row per minute; whether a
`minute(s)` request returns 60 rows an hour with 15 populated, or only four, is open. This integration
requests `days` only — a product decision (PLAN #65), not an API limitation.

Consequence: **no live power sensor**. Today's values do not exist yet, and yesterday's may not have
arrived either, so there is nothing to show for "now"; a `W` sensor built from quarter-hour history
would be an average over a past interval, not a reading. Anyone promising a real-time card in this
integration is describing a different product. The newest value Home Assistant can show is 1 to 3 days
old, delivered by the 2 h poll ([ADR-0004](decisions/0004-poll-interval-and-data-lag.md)).

Because the delay varies, the daily sensor's newest value can be 1 to 3 days old and the Energy
dashboard will show a flat tail at the right edge: the last bar stays where it is for a day or two, or
a day is simply missing for now. That is expected — it reflects when CKW published the data, not a
broken integration, a stalled poll or a misconfigured sensor. The value moves when the day is
published, and the next poll picks it up within two hours.

The delay also shapes how history is imported. A day can arrive late, after its nominal date has
passed. The rolling 7-day refetch window ([ADR-0004](decisions/0004-poll-interval-and-data-lag.md))
exists precisely to pick up intervals that appear after their nominal day had passed: every poll
re-reads the last seven days, so a late interval is imported when it shows up and a CKW correction
overwrites the earlier value.

Why not import the finer history that may exist? Because the product is daily consumption in the
Energy dashboard. 96 intervals per day multiply every statistic row, every entity update and every
API request for detail no Energy-dashboard graph uses; the decision and its alternatives belong in the
backlog, not in this document.

## Users sit above locations

The customer response is `customer.User[].locations[].meters[]`, not `customer.locations`. `User`
carries `id`, `name`, `email` and `phone`; each user then carries locations, and each location carries
meters.

Two consequences:

- **Several users can map to locations.** An account can return more than one user, so the integration
  walks every user before it counts delivery points. A single-user assumption silently loses meters.
- **The payload is personal data.** `email` and `phone` must be redacted in diagnostics, together with
  the delivery points that are already masked.

The meter is identified by enums, not free text:

| Field | Values | What it means here |
| --- | --- | --- |
| `model` | `SMART`, `ANALOGUE` | Meter model; the API declares only these two |
| `type` | `import`, `export`, `consumption`, `production` | `export` and `production` identify feed-in, so production can be told from consumption without guessing |
| `fuel` | `elec`, `gas` | `fuel: elec` selects electricity and filters out gas meters |

Meters also carry `deliverypointnumber` (the 33-character Swiss Zählpunkt), `supply_start_date`,
`supply_end_date` and a `custom` object with the free-text `meter_type_indicator`. The customer and
measurement shapes are open: unknown keys and missing keys are legal, so a parser must tolerate both.

## Two tariff buckets: HT and NT

Each interval carries two buckets — high tariff (`_ht`) and low tariff (`_nt`). Both arrive as a
quantity in kWh (`menge_fakturiert_ht`, `menge_fakturiert_nt`) and as an amount in CHF (`betrag_ht`,
`betrag_nt`). The integration's kWh total is always the sum of the two buckets; the HT/NT split is kept
as opt-in detail entities so the default device stays readable, and the CHF amounts are parsed but
reach no entity and no statistic (see "The CHF amounts are a base price, and are not exposed").

HT/NT does **not** follow a fixed clock. The grid operator (Netzbetreiber) defines the switching
times, and they can differ by municipality and change over time. The API does not return the
boundary times — `Unknown — verify with CKW` — so the integration never computes them; it only
reports what each bucket contains.

## Billed quantity, not a physical measurement

`menge_fakturiert_*` is the **billed** quantity for the interval. It is what CKW charges for, not a
snapshot of a physical instantaneous measurement, and the difference matters when a value looks odd:
meter rollover, estimated intervals and corrections all end up in the billed number.

`anzahl_linien_p`, `anzahl_linien_fw` and `anzahl_linien_fb` report how many timeseries stand behind
an interval (physical, billed, blind current). All three are `1` in normal operation, including `_fw`
and `_fb`; `0` is not the normal value and does not mean "no line". A value greater than `1` means
more than one series was folded into that interval — a data quality problem at CKW, and the API does
not say which kind. The integration does not silently sum such an
interval: it logs the flag, exposes it as a diagnostic attribute, skips the interval when importing
statistics, and raises a repair issue so the user knows the total has a hole rather than a wrong
number.

## The CHF amounts are a base price, and are not exposed

`betrag_ht` and `betrag_nt` are **base prices in CHF, without VAT and without surcharges** (grid
fees, levies, municipal charges). The sum is a usable trend, not the number on the invoice — it will
be lower, and the gap is not a bug.

Because that number cannot be reconciled with a bill, the integration parses it and stops there: no
entity, no statistic and no diagnostics field exposes a CHF amount, and the Energy dashboard's cost
graph therefore stays empty for these meters. That is a recorded decision (PLAN.md, decision A2), not
an unfinished feature. A missing or null `betrag_*` is handled like any other unknown field and never
becomes `0.0`; nothing user-facing depends on it.

## Aggregations are server-side

The API accepts `year(s)`, `month(s)`, `week(s)`, `day(s)` and `minute(s)`. The integration always
requests `days` and aggregates locally. Two reasons:

- A daily bucket should correspond to **one local day in `Europe/Zurich`**, including the 23 h and
  25 h days around a DST change. That boundary is an **inference, not a documented fact**: the API
  returns bare UTC timestamps, CKW's examples sit on quarter hours rather than midnight, and the only
  hint is the legal "read once per day" rule. Server-side `month(s)` would hand us buckets whose
  boundaries are equally undocumented, and a DST day could split or double-count. We therefore keep
  the assumption and import one statistic per local day.
- The rolling 7-day window ([ADR-0004](decisions/0004-poll-interval-and-data-lag.md)) and the 730-day
  backfill need one shape of response, independent of how the user later looks at the data.
  Statistics are imported per local day ([ADR-0002](decisions/0002-statistics-strategy.md)).

## Supported devices

Quality-scale rule `docs-supported-devices`.

| Supported | Not supported |
|---|---|
| CKW smart meters whose delivery point (`deliverypointnumber`) is returned by `GET /smartmeter/customer` — i.e. inside the CKW supply area with a smart meter installed. | Legacy electromechanical meters (no daily aggregate exists to fetch). |
| Electricity meters with `fuel: elec`. | Gas meters (`fuel: gas`); water and district heat are not part of the API's fuel enum. |
| Accounts with several delivery points, including accounts with several users — each delivery point becomes its own device. | Meters of other Swiss grid operators (needs a provider registry, out of scope). |

A meter that CKW knows but the API does not list cannot be supported by guessing a delivery point
number: the 33-character Zählpunkt is the only key, and it comes from `/smartmeter/customer`.

## Rate limiting paces the backfill

The public host advertises four requests per roughly one-second window (`x-ratelimit-limit: 4`). The
meter host sends no rate-limit headers without a token, so whether it shares that limit is open. If it
does, a **730-day backfill must be paced**: 730 daily requests cannot be fired as a tight loop. They
arrive over several minutes, and a first setup therefore shows history filling in rather than all at
once. The same limit bounds how aggressively the 7-day window may be refetched after an error.

## Update cadence

Quality-scale rule `docs-data-update`.

- Poll interval: 2 h by default, configurable 1–12 h in the options flow. The interval is also the
  session keep-alive, and the refresh token lives 6 h, so anything at or above 6 h breaks the login
  between polls unless CKW grants `offline_access` — [session lifetime](session-lifetime.md), and
  follow-up #53 for the range itself.
- Each poll refetches the last **7 days** (`days` aggregation), so missed polls and CKW corrections
  repair themselves; anything older only changes through the backfill.
- Data arrives **1 to 3 days late** — the newest sensor value is not today's, and usually not
  yesterday's either; the contract only promises readings up to yesterday.
- Backfill at first setup: **730 days**, paced by the rate limit (see above), imported idempotently and
  re-importable after a recorder wipe or a meter exchange
  ([backfill history](../how-to/backfill-history.md)).

## Known limitations

Quality-scale rule `docs-known-limitations`.

| Limitation | What it means for you |
|---|---|
| 1–3 day lag | The newest value is 1 to 3 days old, and how old varies by meter and day; there is no workaround, only patience. |
| No live values | No power (`W`) for *now*, no current; the freshest value is 1–3 days old. Minute-level history is not imported (PLAN #65). |
| Paced backfill | First setup imports 730 days over several minutes rather than instantly; the history fills in. |
| No cost entities | The CHF amounts CKW returns are parsed but never exposed as an entity, a statistic or a diagnostics field: they are base prices without VAT and surcharges, so they would not match the invoice. |
| CKW only | Other grid operators need their own integration; this one has one provider. |
| No feed-in tariff calculation | Production or PV feed-in metering is not implemented (tracked separately). |
| 7-day self-healing window | A gap older than 7 days does not repair itself; it needs the backfill. |
| Sessions last months, not forever | CKW caps a session at roughly 2–3 months — their own app behaves the same — and a long outage can end it sooner. An occasional reauthentication is normal; the imported history is kept. |

## Related

- [Architecture](architecture.md) — where the coordinator and the statistics import sit.
- [ADR-0002](decisions/0002-statistics-strategy.md) — why history is imported as entity statistics.
- [ADR-0004](decisions/0004-poll-interval-and-data-lag.md) — why 2 h, why 7 days, and the observed 1–3 day lag.
- [ADR-0009](decisions/0009-resolution-and-storage.md) — hourly statistics, quarter-hour states, and why no sub-hourly history can be imported.
- [Session lifetime](session-lifetime.md) — how long a login holds, and what forces a reauth.
- [OpenEnergy API reference](../reference/ckw-openenergy-api.md) — every field named on this page.
- [Glossary](../reference/glossary.md) — HT/NT, Zählpunkt, billed vs physical quantity.
- [Entities](../reference/entities.md) — which sensor carries which number, with units and classes.
- [Backfill history](../how-to/backfill-history.md) · [Debug a connection](../how-to/debug-connection.md)
- [PLAN.md](../../PLAN.md) — scope, out-of-scope items and the risk register.
