# Install or remove the integration

This page answers: how do I install the CKW Smart Meter integration in Home Assistant, and how do I
get rid of it and its history again?

Two install routes, one removal route. The removal keeps your statistics unless you delete them
explicitly, which is what most people want after a repair and not what anyone wants after abandoning
the integration.

## Through HACS

This route needs a **public** repository with a release, which is what v0.1.0 is: HACS fetches
releases anonymously, so a private repository or an untagged `main` answers `404`. Both routes below
work today; the manual one stays documented for a Home Assistant without HACS.

HACS, now Home Assistant Marketplace, does not know this integration by default, so add the
repository once:

1. Open **HACS** in the sidebar.
2. Open the three-dot menu at the top right and choose **Custom repositories**.
3. Enter `https://github.com/Maladus/ha-ckw-smartmeter`, choose category **Integration** and confirm.
4. Search HACS for **CKW Smart Meter** and open its page.
5. Choose **Download**, then restart Home Assistant.

You should now see the integration in Settings → Devices & services → **Add integration**.

## Manually

1. Download the repository (Code → Download ZIP, or a `git clone`).
2. Copy the folder `custom_components/ckw` into `/config/custom_components/` of your Home Assistant
   installation, so that `custom_components/ckw/manifest.json` exists.
3. Restart Home Assistant.

You should now see the integration in Settings → Devices & services → **Add integration**.

## Add the integration

Both routes end in the same place, and the walkthrough with the expected result at every click is
[tutorial: getting started](../tutorial/getting-started.md).

1. Go to Settings → Devices & services → the three-dot menu → **Application credentials** → **Add
   application credentials** → **CKW Smart Meter** and paste the client id CKW issued you. Without
   it, the next step stops with "Add the CKW OAuth application credentials before setting up the
   integration."
2. Go to Settings → Devices & services → **Add integration** → **CKW Smart Meter** and sign in at
   CKW. The sign-in returns through `https://my.home-assistant.io/redirect/oauth` when your instance
   has the `my` integration, which nearly every installation does — Cloud or not, no Nabu Casa
   subscription involved. Which URIs CKW must have registered, and what a mismatch looks like, is in
   [reference: redirect URIs](../reference/oauth-redirects.md).

You should now see one entry per CKW account, with one device per Zählpunkt under it.

## Update

- **HACS route:** HACS lists a new release under **Updates**; install it and restart Home Assistant.
- **Manual route:** replace the whole `custom_components/ckw` folder with the new one and restart.
  Do not merge two versions by hand: a stale file next to a new one is the usual cause of a
  component that refuses to load.

Your entry, its devices and its long-term statistics survive both routes, because they live in the
Home Assistant configuration and the recorder, not in the component folder.

## Remove

1. Go to Settings → Devices & services, open the **CKW Smart Meter** entry, open its three-dot menu
   and choose **Delete**.
2. Remove the code: delete the folder `/config/custom_components/ckw`, and remove the custom
   repository again in HACS → three-dot menu → **Custom repositories** if you added it there.
3. Remove the application credential from Settings → Devices & services → three-dot menu →
   **Application credentials** if you are not going to reinstall.
4. Restart Home Assistant.

You should now see neither the integration nor its devices and entities in Settings → Devices &
services.

### Delete the leftover statistics and entities

Deleting the entry removes the device and its entities from the entity registry. The **long-term
statistics stay** — that is by design, because the imported history is what the Energy dashboard uses
and a repair should not throw it away. They also keep their row in the Energy dashboard
configuration, which then shows a dead source.

To delete them as well:

1. Go to **Developer Tools** → **Statistics**.
2. Search for the meter's entity id, for example `sensor.smart_1234_consumption_total`, and delete
   each of its statistics in turn.
3. Remove the same sensor from Settings → Dashboards → Energy → Electricity grid, or delete the
   Energy dashboard configuration entry it left behind.

You should now see no CKW row in Developer Tools → Statistics and no CKW source in the Energy
dashboard.

## Related

- [Getting started](../tutorial/getting-started.md) — the whole path from install to a populated
  Energy dashboard.
- [Request the CKW client](request-ckw-client.md) — the client id the "Add the integration"
  step requires.
- [Redirect URIs](../reference/oauth-redirects.md) — the URIs the sign-in step needs registered,
  with and without Home Assistant Cloud.
- [Reauthenticate](reauthenticate.md) — what to do when a working install stops accepting the CKW
  login, without deleting anything.
- [Backfill history](backfill-history.md) — re-importing the 730 days after a meter exchange or a
  recorder wipe.
- [Entities](../reference/entities.md) — the entity and statistic ids the removal section names.
- [Documentation map](../README.md) — where this page sits among the quadrants.
