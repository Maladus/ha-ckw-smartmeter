# Get live values next to the archive

This page answers: how do I get live values when the CKW integration only has data from 1–3 days
ago?

The short answer: add a local source on the meter itself and keep the CKW integration for billing.
The integration cannot become faster, but nothing stops a second sensor from being live.

## Why nothing in this integration is live

Your grid operator (Netzbetreiber) may legally read your meter **once per day**. The CKW OpenEnergy
API therefore serves one aggregate per closed day per Zählpunkt, and in practice the newest value
arrives **1 to 3 days late** — "up to yesterday" is the contract's ceiling, not the observed arrival
time. No poll interval, no option and no dashboard card changes that: a value that has not been
published yet cannot be fetched. Everything this integration stores is an archive of billed days.

If you need a value for *now* — to switch a load, to see whether the oven is on, to catch a spike —
you need a second source that measures at home. Two common ones follow.

## Option A — a local reader on the meter (P1 / HAN port or IR head)

Many Swiss smart meters expose a customer interface: a P1/HAN port (RJ12) or an optical IR interface
under the display. A small reader on that port gives you the meter's own readings — current power,
and often the register totals — every few seconds to a few minutes, locally, without touching the
API.

1. Find out which interface your meter has and whether it is enabled. The Netzbetreiber or the
   meter's datasheet says so; on some models the port must be activated first.
2. Pick a reader for that interface (P1-to-Ethernet/Wi-Fi, or an IR reading head on the optical
   port). Prefer one with a local push (MQTT, ESPHome, a local API) over a vendor cloud.
3. Bring it into Home Assistant as a power sensor (`device_class: power`, unit W) — through an
   existing integration, ESPHome, or MQTT.
4. Check one thing before you trust it: does the sensor report **import minus export**, or separate
   directions? With PV feed-in, a single signed value and two separate values behave differently in
   automations.

This is the most accurate of the two options, because it reads the same meter CKW bills from.

## Option B — a CT clamp or a smart plug on the main circuit

No meter port, or no wish to open the meter cabinet? A clamp-on current transformer around the
incoming live conductor, or a smart plug on a single circuit, measures current locally and reports
power every few seconds.

1. Choose the placement first: the whole service entrance (whole-house power) or one circuit (one
   appliance). A clamp on the main conductor needs an electrician in most installations — do not open
   a distribution board yourself.
2. Install the clamp or plug, pair it with Home Assistant, and confirm it reports a power value with
   a sane magnitude at a known load (a 2 kW kettle should show roughly 2 kW).
3. Rename it so it cannot be confused with the archive: for example
   `sensor.house_power_live`, not `sensor.ckw_consumption`.

A clamp measures apparent power and is not revenue-grade; expect a few percent of systematic error
and a phase-dependent drift. That is fine for switching, not for billing.

## How to combine the two

The split is one sentence: **the live sensor drives automations and dashboards, the CKW integration
stays the billing-accurate archive.** Give them different jobs and never compare them row by row.

- **Automations** watch the live sensor only. It is the only one that can react to something
  happening now.
- **The Energy dashboard** keeps the CKW statistics, because those are the billed daily values, two
  years deep and consistent with your invoice's base price.
- **Reporting** reads the archive. "How much did last month cost" is a statistics question, not a
  live one.

One example, using the live sensor for switching and the archive for reporting:

```yaml
automation:
  - alias: "Water heater: run on PV surplus"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.house_power_live      # the local reader, Watts
        below: -1500                            # exporting 1.5 kW => surplus
        for: "00:10:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.ckw_consumption_yesterday   # the CKW archive, kWh
        above: 0                                      # archive present, integration healthy
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.water_heater
```

The trigger uses instantaneous power, because that is the only value that says "surplus right now".
The condition only checks that the archive is alive — it does not compare the two numbers. A monthly
report then reads `sensor.ckw_consumption_total` and the statistics, never the live sensor.

## Why the two numbers will not match

That is expected, and it is not a bug in either source. Four reasons, roughly in order of size:

| Difference | What it means |
| --- | --- |
| Instantaneous vs billed | The live sensor samples a moment; the CKW value is the quantity the meter accumulated and CKW billed over the interval. Sampling misses or double-counts edges, and the meter may estimate or correct an interval. |
| HT/NT split | The archive splits each day into high and low tariff at times your Netzbetreiber defines. A live sensor knows nothing about tariffs, so a live total and a billed total are not the same quantity. |
| Base price without VAT | CHF amounts from CKW are base prices: no VAT, no grid fees, no levies, no municipal charges. A cost you compute from a live sensor will be lower and differently composed. |
| Different measurement point | A clamp measures what passes the conductor it is on; the meter measures the whole delivery point. A plug on one circuit measures one appliance. |

Expect the archive to trail the live sensor by days and the totals to drift by a few percent. Neither
number is "the right one": the live sensor is right about now, the archive is right about the bill.

## Do not

- **Do not lower the poll interval to chase freshness.** The API publishes once a day; polling every
  few minutes returns the same rows, costs API calls against a public rate limit (four requests per
  roughly one second, shared by everyone on that host), and risks getting your client throttled. It
  buys nothing.
- **Do not expect today's values, at any setting.** Today has not been published. The newest value
  stays 1–3 days old.
- **Do not treat the live sensor as billing data.** It is not revenue-grade, it does not know
  tariffs, and it is not your invoice.
- **Do not compare the two series in one chart and call the gap an error.** Chart the live sensor for
  now and the statistics for the past; if you need both on one screen, label them.
- **Do not open a distribution board for a clamp without an electrician.** The meter cabinet is not
  a DIY location, and a P1/IR reader is the safer route where it exists.

## Related

- [Data model](../explanation/data-model.md) — why the values arrive 1–3 days late, what HT/NT and
  the CHF base price mean.
- [Quarter-hour history in Home Assistant](../explanation/quarter-hour-history.md) — how much
  15-minute detail Home Assistant can hold, and why it cannot be backfilled.
- [Entities](../reference/entities.md) — which sensor carries which number, unit and class.
- [Backfill history](backfill-history.md) — re-running the import for a meter that came back with gaps.
- [Install](install.md) — adding the integration and removing it again.
- [PLAN.md](../../PLAN.md) — the "live data" decision and the rows behind this page (#155, #156, #157).
