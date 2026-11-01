# Glossary

This page answers: what each CKW and Home Assistant term used in this project means, in one sentence.

German is kept only for terms that CKW itself uses (Zählpunkt, Hochtarif/Niedertarif, Blindstrom,
Netzbetreiber); every definition below is the single wording this project uses elsewhere in code and docs.

| Term | Definition | Appears in |
| --- | --- | --- |
| Aggregation (`aggregation`) | The resolution a measurement request asks for — one of `year`, `years`, `month`, `months`, `week`, `weeks`, `day`, `days`, `minute`, `minutes` — which decides how much time one returned measurement element covers. | `GET /smartmeter/measurement/{datum_von_YYYYMMDD}/{datum_bis_YYYYMMDD}/{aggregation}`; `ckw-openenergy-api.md` |
| `anzahl_linien_fb` | The number of blind-current timeseries the meter delivered for one interval; `1` is normal and more than `1` usually indicates a data quality issue. | `GET /smartmeter/measurement/…` → `measurements[].anzahl_linien_fb` |
| `anzahl_linien_fw` | The number of billable timeseries the meter delivered for one interval; `1` is normal and more than `1` usually indicates a data quality issue. | `GET /smartmeter/measurement/…` → `measurements[].anzahl_linien_fw` |
| `anzahl_linien_p` | The number of physical timeseries the meter delivered for one interval; `1` is normal and more than `1` usually indicates a data quality issue. | `GET /smartmeter/measurement/…` → `measurements[].anzahl_linien_p` |
| Base price | The CHF energy amount in `betrag_ht` and `betrag_nt`, which excludes VAT and excludes surcharges, so it is never the amount the customer is finally billed. | `GET /smartmeter/measurement/…` → `measurements[].betrag_ht`, `measurements[].betrag_nt` |
| Billed quantity | The energy CKW actually bills for an interval, delivered as `menge_fakturiert_ht` (HT) and `menge_fakturiert_nt` (NT) in kWh, as opposed to the physical quantity the meter recorded. | `GET /smartmeter/measurement/…` → `measurements[].menge_fakturiert_ht`, `measurements[].menge_fakturiert_nt`; `entities.md` |
| Blind current (Blindstrom) | The reactive part of the metered current that does no useful work, counted per interval by `anzahl_linien_fb`. | `GET /smartmeter/measurement/…` → `measurements[].anzahl_linien_fb` |
| CKW Single Sign-On (CKW SSO) | CKW's Keycloak identity provider (`https://id.ckw.ch/realms/CKW-prod`, test realm `CKW-dev`) that issues the JWT this integration sends as `Authorization: Bearer <JWT>`. | `ckw-openenergy-api.md` → Authentication; `../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md` |
| `deliverypointnumber` (Zählpunkt) | The 33-character Swiss metering point identifier under which the API reports a meter and its measurements. | `GET /smartmeter/customer` → `customer.locations[].meters[].deliverypointnumber`; `GET /smartmeter/measurement/…` → `[].deliverypointnumber`; `entities.md` |
| Energy dashboard | Home Assistant's built-in dashboard for grid consumption, return and cost, which reads long-term statistics rather than a live power value. | `../explanation/data-model.md`; `../tutorial/getting-started.md` |
| HT (high tariff, Hochtarif) | The day-time tariff band whose amount arrives as `betrag_ht` and whose billed quantity arrives as `menge_fakturiert_ht`; the switching times are not part of the API contract. | `GET /smartmeter/measurement/…` → `measurements[].betrag_ht`, `measurements[].menge_fakturiert_ht`; `entities.md` |
| Long-term statistics | Home Assistant's recorder-backed, never-purging statistics series that keep energy history for years after the raw states are gone. | `../explanation/data-model.md`; `../how-to/backfill-history.md`; `entities.md` |
| NT (low tariff, Niedertarif) | The night-time tariff band whose amount arrives as `betrag_nt` and whose billed quantity arrives as `menge_fakturiert_nt`; the switching times are not part of the API contract. | `GET /smartmeter/measurement/…` → `measurements[].betrag_nt`, `measurements[].menge_fakturiert_nt`; `entities.md` |
| OpenEnergy API | The CKW OpenEnergy Client API — MuleSoft Anypoint, Axpo group, RAML 1.0, version group `v1` — that serves customer core data, measurements and dynamic prices over HTTPS. | `ckw-openenergy-api.md`; `../how-to/request-ckw-client.md` |
| Physical quantity | The energy a meter recorded, which can differ from the billed quantity in `menge_fakturiert_ht` and `menge_fakturiert_nt`; the contract fields transcribed here expose only the billed side. | `GET /smartmeter/measurement/…` → `measurements[].menge_fakturiert_ht`, `measurements[].anzahl_linien_p` |
| Statistic id | Home Assistant's identifier for one long-term statistics series, derived from the entity's unique ID, which this project keeps append-only so a rename never splits the history. | `../explanation/data-model.md`; `entities.md`; `conventions.md` |

## Related

- [OpenEnergy API reference](ckw-openenergy-api.md) — the endpoints and fields every term above comes from.
- [Data model](../explanation/data-model.md) — why readings arrive 1 to 3 days late, HT/NT, the quality flags,
  and why there is no live power sensor.
- [Conventions](conventions.md) — the crosscutting rules (error mapping, redaction, Europe/Zurich) that use
  these terms.
- [Entities](entities.md) — the sensors and statistics the terms end up as.
- [Configuration parameters](configuration.md) — what the user enters when setting the integration up.
