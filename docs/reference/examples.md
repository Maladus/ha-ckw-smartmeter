# Examples

This page answers: what can I copy into an automation or a dashboard once the CKW integration is set
up, and which entity ids and units does each example assume?

Five examples, each self-contained. They read the entities the integration actually creates; replace
the `1234` in every entity id with the last four characters of your Zählpunkt. The device page shows
the full pattern, and [entities](entities.md) lists every sensor, unit and attribute.

## Entity ids used here

| Entity id | Unit | Enabled by default | What it holds |
| --- | --- | --- | --- |
| `sensor.smart_1234_consumption_total` | kWh | yes | Cumulative billed consumption since the integration first saw the meter; carries `publication_delay_h`, `last_published_interval`, `quality_intervals` and, while stale, `stale_since` |
| `sensor.smart_1234_consumption_yesterday` | kWh | yes | The newest complete day CKW has published, 1–3 days old |
| `sensor.smart_1234_last_update` | timestamp | yes | When the last poll succeeded |
| `sensor.smart_1234_last_published_interval` | timestamp | yes | End of the newest day CKW has published |
| `sensor.smart_1234_consumption_high_tariff` | kWh | **no** | Cumulative high-tariff part of the total |
| `sensor.smart_1234_consumption_low_tariff` | kWh | **no** | Cumulative low-tariff part of the total |

Find the real ids under **Developer Tools → States** by searching for `ckw` or the meter's device
name. The entity id is also the statistic id used by the Energy dashboard; a rename after setup splits
the series, so use the ids as they are.

Every example here works on the newest **published** data. None of them can react to a value for
today: the meter is read once a day and CKW publishes 1–3 days late
([data model](../explanation/data-model.md)).

## Example 1 — notify when yesterday's consumption exceeds a threshold

Fires when the newest complete day crosses the limit. It notifies once per crossing, not every day
while the value stays above it.

```yaml
automation:
  - alias: "CKW: newest day above 20 kWh"
    id: ckw_yesterday_above_threshold
    triggers:
      - trigger: numeric_state
        entity_id: sensor.smart_1234_consumption_yesterday
        above: 20
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "CKW consumption"
          message: >-
            The newest published day used
            {{ trigger.to_state.state | float(0) | round(1) }} kWh
            (threshold 20 kWh).
```

**Needs:** `consumption_yesterday` (enabled by default, state in kWh) and a `notify` service. Replace
`notify.mobile_app_phone` with the service of your phone or push app; the threshold is in kWh.

**Cannot:** it reacts to the newest complete published day, which is 1–3 days old, so it is a report,
not a live alarm. It also does not repeat while the value stays above 20 kWh.

## Example 2 — notify when new daily data arrives, with the lag

Fires when `last_published_interval` moves to a newer day. The message shows the publication lag from
the total's attribute, so a slow CKW feed is visible in the notification itself.

```yaml
automation:
  - alias: "CKW: new daily data arrived"
    id: ckw_new_daily_data
    triggers:
      - trigger: state
        entity_id: sensor.smart_1234_last_published_interval
        not_from:
          - unknown
          - unavailable
        not_to:
          - unknown
          - unavailable
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "CKW data arrived"
          message: >-
            Newest published interval ends {{ trigger.to_state.state }}.
            Lag: {{ state_attr('sensor.smart_1234_consumption_total', 'publication_delay_h') | float(0) | round(1) }} h.
            Newest complete day: {{ states('sensor.smart_1234_consumption_yesterday') }} kWh.
```

**Needs:** `last_published_interval`, `consumption_total` and `consumption_yesterday` (all enabled by
default), plus a `notify` service. The two timestamp entities are compared as states, the lag is in
hours and the daily value in kWh.

**Cannot:** it cannot announce a day CKW has not published. If the feed stalls, the automation stays
quiet — that is what [Example 4](#example-4--warn-when-the-publication-delay-crosses-72-h) is for.

## Example 3 — dashboard card with the total and the publication delay

A compact card for the meter page. The first card shows the numbers and the diagnostic timestamps; the
second renders the attributes that tell you whether the feed is healthy.

```yaml
type: vertical-stack
cards:
  - type: entities
    title: CKW smart meter
    entities:
      - entity: sensor.smart_1234_consumption_total
        name: Consumption total
      - entity: sensor.smart_1234_consumption_yesterday
        name: Newest complete day
      - entity: sensor.smart_1234_last_published_interval
        name: Newest published interval
      - entity: sensor.smart_1234_last_update
        name: Last successful poll
  - type: markdown
    content: |
      - **Publication delay:** {{ state_attr('sensor.smart_1234_consumption_total', 'publication_delay_h') | float(0) | round(1) }} h
      - **Flagged intervals:** {{ state_attr('sensor.smart_1234_consumption_total', 'quality_intervals') | int(0) }}
      - **Last successful poll:** {{ states('sensor.smart_1234_last_update') }}
```

**Needs:** the four core entities of one meter, all enabled by default: kWh values and timestamp
states, with `publication_delay_h` in hours and `quality_intervals` as a count. Add the card in **Edit
dashboard → Add card → Manual** and paste the YAML.

**Cannot:** the card cannot show a live value; it reads the last poll and the last published day, so a
delay of 1–3 days is expected and the attributes go unavailable with the entity after a failed poll.

## Example 4 — warn when the publication delay crosses 72 h

Watches the total's `publication_delay_h` attribute directly. The integration itself logs a warning at
96 h and raises the `stale_publication` repair issue after 4 days without a new day; this automation
warns earlier, on your own threshold.

```yaml
automation:
  - alias: "CKW: publication delay above 72 h"
    id: ckw_publication_lag_72h
    triggers:
      - trigger: numeric_state
        entity_id: sensor.smart_1234_consumption_total
        attribute: publication_delay_h
        above: 72
    actions:
      - action: persistent_notification.create
        data:
          title: "CKW data is late"
          message: >-
            The newest published CKW interval is
            {{ state_attr('sensor.smart_1234_consumption_total', 'publication_delay_h') | float(0) | round(1) }} h old.
            1–3 days are normal; check the CKW portal if this lasts.
          notification_id: ckw_publication_lag
```

**Needs:** `consumption_total` (enabled by default, state in kWh) with its `publication_delay_h`
attribute in hours. Use a `notify` action instead of `persistent_notification.create` if you want the
warning on your phone.

**Cannot:** it cannot tell a late CKW publication from a broken poll; while a poll is failing the
entity is unavailable and the numeric trigger does not fire. It also cannot make CKW publish sooner.

## Example 5 — a load-shifting sketch with the HT/NT split

**This is a sketch, not a control loop.** The integration reports the cumulative high- and low-tariff
totals; it does not expose the tariff boundaries, and the newest day is 1–3 days old. The example
learns the long-run low-tariff share and suggests shifting a flexible load; replace the notification
with the switch or script you actually want to postpone.

```yaml
template:
  - sensor:
      - name: "CKW low-tariff share"
        unique_id: ckw_low_tariff_share
        unit_of_measurement: "%"
        state_class: measurement
        state: >-
          {% set ht = states('sensor.smart_1234_consumption_high_tariff') | float(0) %}
          {% set nt = states('sensor.smart_1234_consumption_low_tariff') | float(0) %}
          {% set total = ht + nt %}
          {{ (nt / total * 100) | round(1) if total > 0 else 'unknown' }}

automation:
  - alias: "CKW: suggest shifting a load (sketch)"
    id: ckw_shift_load_sketch
    triggers:
      - trigger: state
        entity_id: sensor.smart_1234_last_published_interval
        not_from:
          - unknown
          - unavailable
        not_to:
          - unknown
          - unavailable
    conditions:
      - condition: numeric_state
        entity_id: sensor.ckw_low_tariff_share
        below: 40
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "CKW load shifting"
          message: >-
            Only {{ states('sensor.ckw_low_tariff_share') }} % of the metered energy is low tariff.
            Consider running the dishwasher or the heat pump in the NT window.
```

**Needs:** enable **Consumption high tariff** and **Consumption low tariff** first — both are
registered disabled by default, and both report cumulative kWh. Open the meter's device page, open each
entity, choose **Enable**, and wait for the next poll. Then reload templates or restart Home Assistant;
the sketch's own sensor is a percentage.

**Cannot:** it cannot switch on the current tariff window. The HT/NT boundary times are not in the API
and the share is cumulative, so this is a prompt for a human or another automation, never a live
control loop. For now, use the live sensor from [live values next to the archive](../how-to/live-and-archive.md)
for switching decisions.

## Related

- [Entities](entities.md) — every entity, unit, attribute, statistic id and Energy-dashboard rule.
- [Configuration](configuration.md) — the poll interval, the backfill option and the `ckw.backfill`
  action.
- [Data model](../explanation/data-model.md) — the 1–3 day lag, HT/NT semantics and the known
  limitations.
- [Debug a connection](../how-to/debug-connection.md) — what to read when an example stops firing.
- [Live values next to the archive](../how-to/live-and-archive.md) — the local sensor that can drive a
  real control loop.
- [Quality scale](quality-scale.md) — the `docs-examples` rule this page closes (#128).
- [Documentation map](../README.md) — where this page sits among the quadrants.
