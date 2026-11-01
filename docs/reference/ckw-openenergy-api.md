# CKW OpenEnergy Client API

This page answers: what the CKW OpenEnergy API exposes — endpoints, fields, limits and error codes.

A transcription of the published **CKW OpenEnergy Client API** contract (MuleSoft Anypoint, Axpo group;
RAML 1.0, version group v1). Lookup only — procedures live in `../how-to/`, the reasoning in
`../explanation/`. Field names are reproduced exactly as the API sends them, including the German ones.
Where the contract does not state a value, a type or a format, the cell says
`Unknown — verify with CKW` rather than a plausible guess.

## Authentication

Identity provider: **CKW Single Sign-On**, a Keycloak realm. Production realm
`https://id.ckw.ch/realms/CKW-prod`; test realm `CKW-dev` (`https://id.ckw.ch/realms/CKW-dev`).

Every API request carries:

```text
Authorization: Bearer <JWT>
```

**Browser flow** — OAuth2 authorization-code with PKCE, registered as a public client, so there is no
client secret.

- Token endpoint: `https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/token`
- Refresh: `grant_type=refresh_token`
- Documented lifetimes (CKW login guide): access token **600 s**, refresh token **21600 s**
- Client id, scopes and the exact PKCE parameters: `Unknown — verify with CKW`

**Headless flow** — for clients without a browser:

1. `POST https://id.ckw.ch/realms/CKW-prod/otp/login` with the body below returns `204` and sends an
   SMS one-time code.
2. The token endpoint then accepts `grant_type=password` with `code=<OTP>`.

Request body of `POST /otp/login`:

| `username` | `password` | `clientId` | `localeCode` |
| --- | --- | --- | --- |
| CKW user name | CKW password | OAuth client id | Locale of the SMS text |

OTPs are single use. Whether the two flows share the same client id and whether the headless flow is
enabled for every customer: `Unknown — verify with CKW`.

Token, password and OTP handling rules are in `../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md`
and `conventions.md`.

## Base URL and versioning

```text
https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1
```

The version group `v1` is the `/api/v1` path segment; it is not a header or a query parameter. A test or
sandbox base URL is not part of the transcribed contract (`Unknown — verify with CKW`; only the test
realm `CKW-dev` is documented).

## Endpoint reference

Three endpoints are documented. All of them are `GET`, take no request body, and return the same error
response set — `400`, `401`, `403`, `408`, `429`, `500`, `503`. A response body schema for those codes is
not part of the contract, so the body cannot be used to tell the causes apart
(`Unknown — verify with CKW`).

Where a table cell says `Unknown — verify with CKW`, the contract states neither a type nor a format for
that field; the field itself is part of the response.

### `GET /smartmeter/customer`

Core data: the customer, the locations of the delivery points, and the meters at each location.
Parameters: none documented.

The response is an object `customer` containing the array `locations`:

| `locations[]` | `address` | `meters[]` |
| --- | --- | --- |
| Array of location objects, the top level of the response | Object nested in each location | Array of meter objects nested in each location |

Location fields:

| `id` | `gpnr` | `address` |
| --- | --- | --- |
| `Unknown — verify with CKW` | `Unknown — verify with CKW` | Object, see the address fields below |
| Location identifier | Business partner number (GPNR); exact expansion `Unknown — verify with CKW` | Postal address of the location |

Address fields:

| `street_address` | `postal_code` | `city` |
| --- | --- | --- |
| `Unknown — verify with CKW` | `Unknown — verify with CKW` | `Unknown — verify with CKW` |
| Street and house number | Postal code | City |

Meter identity fields:

| `id` | `model` | `type` | `fuel` |
| --- | --- | --- | --- |
| `Unknown — verify with CKW` | `Unknown — verify with CKW` | `Unknown — verify with CKW` | `Unknown — verify with CKW` |
| Meter identifier | Meter model | Meter type | Energy carrier; which values occur is `Unknown — verify with CKW` |

Meter supply fields:

| `deliverypointnumber` | `supply_start_date` | `supply_end_date` |
| --- | --- | --- |
| 33-character string, the Swiss Zählpunkt | `Unknown — verify with CKW` | `Unknown — verify with CKW` |
| Delivery point the measurements are reported under | Start of the supply period; format `Unknown — verify with CKW` | End of the supply period; format and behaviour for a running supply `Unknown — verify with CKW` |

Documented error responses: `400`, `401`, `403`, `408`, `429`, `500`, `503`. Which condition produces
which code (missing role, wrong audience, unknown customer): `Unknown — verify with CKW`.

### `GET /smartmeter/measurement/{datum_von_YYYYMMDD}/{datum_bis_YYYYMMDD}/{aggregation}`

Consumption and production of **all** meters of the customer in one request — the meters are not filtered
by delivery point. Parameters, all in the path:

| `datum_von_YYYYMMDD` | `datum_bis_YYYYMMDD` | `aggregation` |
| --- | --- | --- |
| Date, format `YYYYMMDD` | Date, format `YYYYMMDD` | Enum, see the aggregation values below |
| Start of the requested range | End of the requested range | Resolution of one measurement element |

Aggregation values (exactly these ten): `year`, `years`, `month`, `months`, `week`, `weeks`, `day`,
`days`, `minute`, `minutes`. Both the singular and the plural spelling are part of the enum; how they
differ from each other is not stated in the transcribed contract (`Unknown — verify with CKW`).

Range limits — maximum span per request, whether the boundaries are inclusive, and the response for an
empty range: `Unknown — verify with CKW`.

The response is an array; one element per delivery point:

| `deliverypointnumber` | `measurements[]` |
| --- | --- |
| 33-character string, the Swiss Zählpunkt | Array of measurement objects |
| Delivery point the following measurements belong to | Readings for one interval at the requested aggregation |

Interval boundaries of a measurement:

| `zeitstempel_von_utc` | `zeitstempel_bis_utc` |
| --- | --- |
| `dateTimeOnly`, UTC | `dateTimeOnly`, UTC |
| Start of the interval | End of the interval |

Amounts of a measurement:

| `betrag_ht` | `betrag_nt` |
| --- | --- |
| `double`, CHF | `double`, CHF |
| Amount for HT, base price **without VAT and without surcharges** | Amount for NT, base price **without VAT and without surcharges** |

Billed quantities of a measurement:

| `menge_fakturiert_ht` | `menge_fakturiert_nt` |
| --- | --- |
| `double`, kWh | `double`, kWh |
| Billed quantity for HT | Billed quantity for NT |

Timeseries counters of a measurement:

| `anzahl_linien_p` | `anzahl_linien_fw` | `anzahl_linien_fb` |
| --- | --- | --- |
| `integer` | `integer` | `integer` |
| Number of physical timeseries behind this interval | Number of billable timeseries behind this interval | Number of blind-current timeseries behind this interval |

For all three counters, `1` is the normal value; a value greater than `1` usually indicates a data
quality issue, so such an interval should not be summed blindly. None of the documented fields says
whether an interval is consumption or production — that distinction is `Unknown — verify with CKW`.

Documented error responses: `400`, `401`, `403`, `408`, `429`, `500`, `503`. Which condition produces
which code: `Unknown — verify with CKW`.

### `GET /energie/dynamische-preise`

Hourly dynamic prices, with German field names. Parameters: none documented.

The field names, their types and the shape of the response are not part of the transcribed facts:
`Unknown — verify with CKW`.

The same data is also published **without authentication** at

```text
https://e-ckw-public-data.de-c1.eu1.cloudhub.io/api/v1/netzinformationen/energie/dynamische-preise
```

That public endpoint is what the companion integration `swiss-dynamic-tariffs` uses; this integration
does not consume it.

Documented error responses: `400`, `401`, `403`, `408`, `429`, `500`, `503`.

## Rate limiting

The limit is enforced **per user**. Every response carries three headers:

| `x-ratelimit-limit` | `x-ratelimit-remaining` | `x-ratelimit-reset` |
| --- | --- | --- |
| Requests allowed per window | Requests still available in the current window | Milliseconds until a new window starts |

The numeric limit and the window length are not stated in the transcribed contract
(`Unknown — verify with CKW`); `429` is among the documented error responses.

## Data availability and constraints

- Readings are available **only up to yesterday**, because the meter may legally be read once per day.
  Nothing more recent than the previous day is returned.
- There is **no real-time power value** and no live consumption figure: the API returns intervals, not a
  current reading.
- Amounts (`betrag_ht`, `betrag_nt`) are base prices: **without VAT and without surcharges**. A sum of
  them is not the invoice total.
- `deliverypointnumber` is exactly 33 characters — the Swiss Zählpunkt.
- `anzahl_linien_p`, `anzahl_linien_fw` and `anzahl_linien_fb` above `1` usually indicate a data quality
  issue.
- Whether older history is retained, how far back it reaches, and whether intervals are ever revised after
  delivery: `Unknown — verify with CKW`.

## Support

- Contact: **<openenergy@ckw.ch>**
- API access is requested through the form at **<https://www.ckw.ch/openenergy>** — free of charge, and
  required by Art. 8a–8d StromVV.
- A `403` because the client is missing an API audience or a role is a registration problem, not a code
  problem: escalate it to the address above, quoting the client id.
- Support hours, response times and a status page: `Unknown — verify with CKW`.

## Related

- [Data model](../explanation/data-model.md) — why readings stop at yesterday, HT/NT, the quality flags,
  and why there is no live power sensor.
- [ADR-0001 — OAuth2 with PKCE vs. pasted code](../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md)
  — why the browser flow is the default and what the fallbacks cost.
- [Entities](entities.md) — the sensors and statistics built from these fields.
- [Configuration parameters](configuration.md) — what the user enters, and what is derived.
- [Glossary](glossary.md) — every German and API term above in one sentence.
