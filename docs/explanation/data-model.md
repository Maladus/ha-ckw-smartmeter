# Data model

This page answers: why the numbers stop at yesterday, what HT/NT and the CHF amounts actually mean,
why some intervals are flagged instead of summed, and which meters this integration can serve.

The integration does not invent a data model — it inherits CKW's. Every field it exposes comes from
the two GET endpoints documented in the [API reference](../reference/ckw-openenergy-api.md); where
that contract is silent, this page says so instead of guessing.

## Data stops at yesterday

The OpenEnergy API serves daily aggregates up to yesterday, never today: one value per delivery point
per closed day. Swiss smart meters record quarter-hour load profiles, and the aggregation enum includes
`minute(s)`, so finer *historical* resolution may well exist. What the transcribed contract guarantees
is daily values up to yesterday; whether `minute(s)` returns populated data, and how far back, is
`Unknown — verify with CKW`. This integration requests `days` only — a product decision (PLAN #65),
not an API limitation.

Consequence: **no live power sensor**. Today's values do not exist yet, so there is nothing to show
for "now"; a `W` sensor built from quarter-hour history would be an average over a past interval, not
a reading. Anyone promising a real-time card in this integration is describing a different product. The
freshest value Home Assistant can show is the last closed day, delivered by the 2 h poll
([ADR-0004](decisions/0004-poll-interval-and-data-lag.md)).

Why not import the finer history that may exist? Because the product is daily consumption and cost in
the Energy dashboard. 96 intervals per day multiply every statistic row, every entity update and every
API request for detail no Energy-dashboard graph uses; the decision and its alternatives belong in the
backlog, not in this document.

## Two tariff buckets: HT and NT

Each interval carries two buckets — high tariff (`_ht`) and low tariff (`_nt`). Both arrive as a
quantity in kWh (`menge_fakturiert_ht`, `menge_fakturiert_nt`) and as an amount in CHF (`betrag_ht`,
`betrag_nt`). The integration's total is always their sum; the HT/NT split is kept as opt-in detail
entities so the default device stays readable.

HT/NT does **not** follow a fixed clock. The grid operator (Netzbetreiber) defines the switching
times, and they can differ by municipality and change over time. The API does not return the
boundary times — `Unknown — verify with CKW` — so the integration never computes them; it only
reports what each bucket contains.

## Billed quantity, not a physical measurement

`menge_fakturiert_*` is the **billed** quantity for the interval. It is what CKW charges for, not a
snapshot of a physical instantaneous measurement, and the difference matters when a value looks odd:
meter rollover, estimated intervals and corrections all end up in the billed number.

`anzahl_linien_p`, `anzahl_linien_fw` and `anzahl_linien_fb` report how many timeseries stand behind
an interval (physical, billed, blind current). A value of 1 is the normal case. A value greater than
1 means more than one series was folded into that interval — usually a data quality issue at CKW,
and the API does not say which kind. The integration does not silently sum such an
interval: it logs the flag, exposes it as a diagnostic attribute, skips the interval when importing
statistics, and raises a repair issue so the user knows the total has a hole rather than a wrong
number.

## Cost is a base price

`betrag_ht` and `betrag_nt` are **base prices in CHF, without VAT and without surcharges** (grid
fees, levies, municipal charges). The sum is a usable trend, not the number on the invoice — it will
be lower, and the gap is not a bug. The Energy dashboard cost graph is a trend line, not a bill.

A `betrag_*` that is missing or null becomes an unknown state (`None`), never `0.0`: unknown cost is
not free electricity.

## Aggregations are server-side

The API accepts `year(s)`, `month(s)`, `week(s)`, `day(s)` and `minute(s)`. The integration always
requests `days` and aggregates locally. Two reasons:

- A daily bucket must correspond to **one local day in `Europe/Zurich`**, including the 23 h and 25 h
  days around a DST change. Server-side `month(s)` would hand us buckets whose boundaries we cannot
  verify, and a DST day would either split or double-count.
- The rolling 7-day window ([ADR-0004](decisions/0004-poll-interval-and-data-lag.md)) and the 730-day
  backfill need one shape of response, independent of how the user later looks at the data.
  Statistics are imported per local day ([ADR-0002](decisions/0002-statistics-strategy.md)).

## Supported devices

Quality-scale rule `docs-supported-devices`.

| Supported | Not supported |
|---|---|
| CKW smart meters whose delivery point (`deliverypointnumber`) is returned by `GET /smartmeter/customer` — i.e. inside the CKW supply area with a smart meter installed. | Legacy electromechanical meters (no daily aggregate exists to fetch). |
| Electricity meters reporting `fuel` values for electricity. | Meters of other Swiss grid operators (needs a provider registry, out of scope). |
| Accounts with several delivery points — each becomes its own device. | Non-electricity `fuel` values (gas, water, district heat). |

A meter that CKW knows but the API does not list cannot be supported by guessing a delivery point
number: the 33-character Zählpunkt is the only key, and it comes from `/smartmeter/customer`.

## Update cadence

Quality-scale rule `docs-data-update`.

- Poll interval: 2 h by default, configurable 1–12 h in the options flow.
- Each poll refetches the last **7 days** (`days` aggregation), so missed polls and CKW corrections
  repair themselves; anything older only changes through the backfill.
- Data is available **until yesterday** — the sensors show the last closed day, never today.
- Backfill at first setup: **730 days**, imported idempotently and re-importable after a recorder
  wipe or a meter exchange ([backfill history](../how-to/backfill-history.md)).

## Known limitations

Quality-scale rule `docs-known-limitations`.

| Limitation | What it means for you |
|---|---|
| One-day lag | Today's consumption is invisible; there is no workaround, only patience. |
| No live values | No power (`W`) for *now*, no current; the freshest value is yesterday's. Minute-level history is not imported (PLAN #65). |
| Base price only | CHF values exclude VAT and surcharges and will not match the invoice. |
| CKW only | Other grid operators need their own integration; this one has one provider. |
| No feed-in tariff calculation | Production or PV feed-in metering is not implemented (tracked separately). |
| 7-day self-healing window | A gap older than 7 days does not repair itself; it needs the backfill. |

## Related

- [Architecture](architecture.md) — where the coordinator and the statistics import sit.
- [ADR-0002](decisions/0002-statistics-strategy.md) — why history is imported as entity statistics.
- [ADR-0004](decisions/0004-poll-interval-and-data-lag.md) — why 2 h, why 7 days, why one day late.
- [OpenEnergy API reference](../reference/ckw-openenergy-api.md) — every field named on this page.
- [Glossary](../reference/glossary.md) — HT/NT, Zählpunkt, billed vs physical quantity.
- [Entities](../reference/entities.md) — which sensor carries which number, with units and classes.
- [Backfill history](../how-to/backfill-history.md) · [Debug a connection](../how-to/debug-connection.md)
- [PLAN.md](../../PLAN.md) — scope, out-of-scope items and the risk register.
