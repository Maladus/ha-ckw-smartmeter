# CKW smart meter for Home Assistant

This custom integration brings your CKW smart-meter electricity consumption and cost into Home
Assistant. It reads daily values from the official CKW OpenEnergy API and feeds the Energy dashboard,
so you get kWh and CHF per day without scraping my.ckw.ch and without handing your CKW password to
Home Assistant.

Status: early development. The first installable release is v0.1.0.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## What you get

- Daily consumption in kWh per delivery point (Zählpunkt), including the HT/NT split
- Daily cost in CHF, as billed by CKW and excluding VAT
- One Home Assistant device per delivery point, with long-term statistics for the Energy dashboard
- Roughly two years of history backfilled on first setup
- Install and update through HACS (now Home Assistant Marketplace)
- German and English UI

There is no live power sensor, and there will not be one: your Netzbetreiber may legally read the
meter once per day. The newest value you see is yesterday.

## Before you start

- [ ] A CKW smart meter, read by CKW as your Netzbetreiber
- [ ] A CKW Single Sign-On account at [id.ckw.ch](https://id.ckw.ch)
- [ ] An OpenEnergy client id from CKW, with your Home Assistant redirect URI registered
- [ ] Home Assistant 2025.8 or newer

The client id is the part that takes time. CKW issues it per customer and needs the redirect URI
before you can log in.

## Quickstart

1. Add this repository to HACS and install the CKW integration.
2. Restart Home Assistant.
3. Add the CKW integration and sign in with your CKW account.
4. Pick your Zählpunkt if you have more than one.
5. Open the Energy dashboard, add the CKW sensors, and let the backfill finish.

The walkthrough with every click and its expected result: [tutorial: getting started](docs/tutorial/getting-started.md).

## How CKW sees your data

CKW hands your metering data to this integration only because you asked for it. Under Art. 8a-8d
StromVV, the Netzbetreiber may pass consumption data to third parties only with the customer's
consent, and CKW requires that consent before it issues the client id. Requesting the client id is
that consent. You can withdraw it at CKW, after which the integration stops receiving data.

Home Assistant keeps the OAuth tokens in your local configuration and refreshes them silently. The
integration never stores your CKW password. Diagnostics exports mask your Zählpunkt and drop tokens.

## Documentation

The documentation follows [Diátaxis](https://diataxis.fr/), four quadrants for four needs. Some pages
below are still being written before v0.1.0.

| I want to | Read |
|---|---|
| learn the integration from scratch | [tutorial: getting started](docs/tutorial/getting-started.md) |
| install or remove it | [how-to: install](docs/how-to/install.md) |
| fix a login that CKW stopped accepting | [how-to: reauthenticate](docs/how-to/reauthenticate.md) |
| look up entities, options or API details | [reference: entities](docs/reference/entities.md), [reference: configuration](docs/reference/configuration.md) |
| understand why it works this way | [explanation: architecture](docs/explanation/architecture.md), [explanation: data model](docs/explanation/data-model.md) |

## License

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Martin Wigger.

## Development

CI runs the pre-commit hooks, the Markdown lint, the Python tests, hassfest and the HACS action on
every push to `main` and every pull request. Jobs whose artefacts do not exist yet are skipped, so the
pipeline stays green while the integration grows. A green CI run is required before a merge.
