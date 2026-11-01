# CKW Smart Meter

Bring your CKW smart-meter electricity consumption into Home Assistant. This integration reads your
daily values from the official CKW OpenEnergy API, so the Energy dashboard gets kWh per day without
scraping my.ckw.ch and without handing your CKW password to Home Assistant. You sign in with your
existing CKW Single Sign-On account; only the resulting token is stored, and it is refreshed silently.

You need a CKW smart meter and an OpenEnergy client id issued by CKW for your Home Assistant address.
Then you install the integration, sign in, pick your Zählpunkt, and the history import fills the Energy
dashboard with roughly two years of billed days. One Home Assistant device per delivery point, in
German or English.

## Features

- Daily consumption per delivery point (Zählpunkt) as long-term statistics for the Energy dashboard
- One device per Zählpunkt, with the HT/NT tariff split available as extra sensors
- A quarter-hour sensor for the current day, for finer charts
- About two years of history imported on first setup, safe to re-run
- Sign in with CKW Single Sign-On, silent token refresh, a reauth prompt when CKW stops accepting the login
- Several CKW accounts side by side, one integration entry each
- Repair notices for a meter exchange, data-quality flags, a stalled publication or a recorder retention
  that is too short
- German and English UI

**Not real-time: data is 1–3 days behind.** Your meter is read once a day and CKW publishes late, so
the newest value is yesterday's or older. This integration is an archive of billed days, not a monitor.
For live power you need a local source on the meter.

There is no live power sensor, and there will not be one. Cost in CHF is read from CKW but is not
exposed as an entity.
