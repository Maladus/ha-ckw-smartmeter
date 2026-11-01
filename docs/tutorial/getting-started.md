# Getting started

This page answers: how do I get from a fresh Home Assistant to my CKW consumption in the Energy
dashboard, in one pass?

One path, no alternatives: request the CKW client id beforehand, install the integration, register
the client id, sign in at CKW, pick your Zählpunkt, and wait for the history. Each step names what
you should see when it worked. Roughly 15 minutes of clicking, plus the time the history import
needs in the background.

## Before you start

- [ ] A CKW smart meter, read by CKW as your Netzbetreiber.
- [ ] A CKW Single Sign-On account at [id.ckw.ch](https://id.ckw.ch).
- [ ] An OpenEnergy client id from CKW, with your Home Assistant redirect URI registered. This is
      the part that takes days, not minutes: follow
      [how-to: request the CKW client](../how-to/request-ckw-client.md) and come back when CKW has
      replied.
- [ ] Home Assistant 2025.8 or newer, on a machine with internet access.
- [ ] HACS installed and working. If it is not, [how-to: install](../how-to/install.md) covers both
      the HACS route and the manual copy.

## Step 1 — Install the integration

Install CKW Smart Meter and restart Home Assistant, as described in
[how-to: install](../how-to/install.md).

You should now see **CKW Smart Meter** in Settings → Devices & services → Add integration, with no
red "invalid config" repair above the integration list.

## Step 2 — Register your client id

Home Assistant asks for the CKW client id before it can start a login. Go to Settings → Devices &
services → the three-dot menu → **Application credentials** → **Add application credentials** →
**CKW Smart Meter**, and paste the client id CKW sent you. Leave the secret empty: CKW registers this
as a public client.

You should now see one entry named **CKW Smart Meter** in the Application credentials list.

## Step 3 — Add the integration

Go to Settings → Devices & services → **Add integration** → **CKW Smart Meter**.

You should now see the **Connect to CKW** dialog, which offers to sign in with your CKW account.

## Step 4 — Log in at CKW

Confirm the dialog. Home Assistant sends you to the CKW Single Sign-On page; sign in there with your
CKW account, and approve the access request if CKW shows one. CKW then sends you back to Home
Assistant.

You should now see the "success" page in Home Assistant; the dialog then finishes the setup and,
with a single meter on the account, the new entry appears in Settings → Devices & services with an
electricity meter under it.

## Step 5 — Pick your Zählpunkt

Home Assistant asks **Choose a meter** when the CKW account returns more than one electricity meter;
with exactly one meter it picks that one and skips the question. The form asks for the **Delivery
point** and says how many electricity meters are available. Pick the Zählpunkt whose consumption you
want in the Energy dashboard.

You should now see the entry's page with one device named after the meter, for example
`SMART *****************************1234`: the meter model plus the masked Zählpunkt.

## Step 6 — Confirm the devices and sensors

Open the integration entry and then the meter device.

You should now see seven entities on that device, four of them enabled:

- **Consumption total** — cumulative kWh since the integration first saw the meter. This is the one
  the Energy dashboard wants.
- **Consumption yesterday** — the kWh of the newest complete day CKW has published.
- **Last update** and **Last published interval** — two diagnostic timestamps: when Home Assistant
  last polled, and the end of the newest day CKW has published.

The three remaining entities — **Consumption high tariff**, **Consumption low tariff** and
**Quarter-hour consumption** — are disabled by default. Leave them alone for now; the
[entities reference](../reference/entities.md) explains what each one carries.

**Consumption total** shows a number and **Last published interval** a date. If both stay empty, CKW
has not returned any measurement for this Zählpunkt yet, and the next poll in about two hours fills
them in.

## Step 7 — Confirm the Energy dashboard shows history

The integration imports roughly 730 days of daily history. That import runs in the background and is
paced by CKW's rate limit, so it takes several minutes rather than being there instantly; the newest
value it can import is 1–3 days old, because CKW publishes late.

Open Settings → Dashboards → Energy → **Electricity grid** → **Grid consumption** → **Add
consumption**, pick the meter's **Consumption total** statistic, and save.

You should now see daily bars for the past two years, and the right edge of the graph ends 1–3 days
before today. That flat tail is CKW's publication delay, not a broken integration: the bars fill in
while the import runs, and the newest day appears within two hours of CKW publishing it.

## What's next

- [Entities](../reference/entities.md) — every sensor with its unit, class, statistic id and update
  cadence, and which of them the Energy dashboard can use.
- [Live values next to the archive](../how-to/live-and-archive.md) — how to see power for *now*,
  which this integration deliberately never shows.
- [Backfill history](../how-to/backfill-history.md) — re-run the 730-day import after a meter
  exchange or a recorder wipe.

## Related

- [Install](../how-to/install.md) — the HACS and manual install this tutorial step 1 links, plus
  removal.
- [Request the CKW client](../how-to/request-ckw-client.md) — the client id and redirect URI this
  tutorial assumes are already issued.
- [Entities](../reference/entities.md) — the seven sensors, their ids and their cadences.
- [Data model](../explanation/data-model.md) — why the newest value is 1–3 days old and what HT/NT
  mean.
- [Live values next to the archive](../how-to/live-and-archive.md) — the local live sensor that pairs
  with this archive.
- [Documentation map](../README.md) — where this page sits among the quadrants.
