# CKW OpenEnergy Client API

This page answers: what the CKW OpenEnergy API exposes — endpoints, fields, limits and error codes.

A transcription of the published **CKW OpenEnergy Client API** contract (MuleSoft Anypoint, Axpo group;
RAML 1.0, version group v1), corrected against the deployed API model measured on 2026-11-01. Lookup
only — procedures live in `../how-to/`, the reasoning in `../explanation/`. Field names are reproduced
exactly as the API sends them, including the German ones.

The measurements and the discrepancies they exposed are recorded in
[the verification page](ckw-openenergy-api-verification.md). This page states the corrected contract;
questions the measurements leave open are collected under [Open questions](#open-questions) instead of
being guessed in a table cell.

## Response shapes are open

The customer and measurement shapes are declared open (`shacl:closed: false`), and the portal warns
that new fields can appear without a major-version bump. Unknown keys and missing keys are both legal.
A parser must ignore what it does not know and must not require a field it does not need, or a CKW
addition becomes an outage.

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
- The redirect URI must be registered at CKW, and the authorize request must return
  `response_mode=query` for Home Assistant's `/auth/external/callback`. The URL CKW documents asks for
  `fragment`, which would leave the authorization code where Home Assistant's callback does not read
  it. See [ADR-0006](../explanation/decisions/0006-auth-measured-constraints.md).

The portal states `grant_type:authorization_code` and that the grant type `password` is not supported.
The realm metadata still advertises `password`, but there is no implementation path for it; the headless
password + SMS-OTP flow from earlier revisions of this page is not available.

## Base URL and versioning

```text
https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1
```

The version group `v1` is the `/api/v1` path segment; it is not a header or a query parameter. Only the
test realm `CKW-dev` is documented; whether a separate test or sandbox base URL exists is open.

## Endpoint reference

Three endpoints are documented. All of them are `GET`, take no request body, and return the same error
response set — `400`, `401`, `403`, `408`, `429`, `500`, `503`. The documented bodies are in
[Error responses](#error-responses).

### `GET /smartmeter/customer`

Core data: the customer, the users of the account, the locations of the delivery points, and the meters
at each location. Parameters: none documented.

The response is an object `customer` whose `User` array sits **above** `locations`:

```text
customer
└─ User[]
   ├─ id, name, email, phone
   └─ locations[]
      ├─ id, gpnr, address {street_address, postal_code, city}
      └─ meters[] {id, model, type, fuel, deliverypointnumber,
                   supply_start_date, supply_end_date, custom {meter_type_indicator}}
```

A client written against `customer.locations` raises `KeyError` on the first live call. Several users
can map to locations, so consumers iterate every user and key meters by `deliverypointnumber`, the key
the measurement response uses.

User fields:

| `id` | `name` | `email` | `phone` |
| --- | --- | --- | --- |
| User identifier | User name | Email address of the user | Phone number of the user |

`email` and `phone` are personal data and **must be redacted in diagnostics**. They are the two fields
nobody listed before the measurement.

Location fields:

| `id` | `gpnr` | `address` |
| --- | --- | --- |
| Location identifier; exact type and format were not measured | Business partner number (GPNR); exact expansion was not measured | Object, see the address fields below |

Address fields:

| `street_address` | `postal_code` | `city` |
| --- | --- | --- |
| Street and house number; exact type and format were not measured | Postal code; exact type and format were not measured | City; exact type and format were not measured |

Meter identity fields:

| `id` | `model` | `type` | `fuel` |
| --- | --- | --- | --- |
| Meter identifier | Enum: `SMART`, `ANALOGUE` | Enum: `import`, `export`, `consumption`, `production` | Enum: `elec`, `gas` |
| Identifies the meter | Meter model | Meter type; `export` and `production` identify feed-in | Energy carrier; `elec` filters out gas meters |

`model` and `type` are the only values the deployed model declares; a strict validator that expects a
free-text model or a type such as `electricity` will reject a real meter.

Meter supply fields:

| `deliverypointnumber` | `supply_start_date` | `supply_end_date` |
| --- | --- | --- |
| 33-character string, the Swiss Zählpunkt, e.g. `CH1003601234500000000000000000000` | Start of the supply period; format was not measured | End of the supply period; format and behaviour for a running supply were not measured |
| Delivery point the measurements are reported under | Start of the supply period | End of the supply period |

Each meter also carries a `custom` object with the free-text `meter_type_indicator`. It is harmless
metadata, but a client that validates strictly will reject it; other keys are legal because the shape
is open.

### `GET /smartmeter/measurement/{datum_von_YYYYMMDD}/{datum_bis_YYYYMMDD}/{aggregation}`

Consumption and production of **all** meters of the customer in one request — the meters are not
filtered by delivery point. Parameters, all in the path:

| `datum_von_YYYYMMDD` | `datum_bis_YYYYMMDD` | `aggregation` |
| --- | --- | --- |
| Date, format `YYYYMMDD` | Date, format `YYYYMMDD` | Enum, see the aggregation values below |
| Start of the requested range | End of the requested range | Resolution of one measurement element |

Aggregation values (exactly these ten): `year`, `years`, `month`, `months`, `week`, `weeks`, `day`,
`days`, `minute`, `minutes`. Both the singular and the plural spelling are part of the enum and are
accepted.

`minute` and `minutes` are the **finest available** resolution, not a promise of one row per minute.
CKW's own sample data is quarter-hourly: every measurement example in the deployed model is 15 minutes
wide, no 1-minute example exists, and the public price feed also returns 96 quarter-hour slots per day.
Whether a `minute(s)` request returns 60 rows per hour with 15 of them populated, or only four rows,
needs one authenticated call and is [open](#open-questions).

Range limits: a range that is too large answers `400` with
`"response too big, please adjust aggregation or duration accordingly"`. The maximum span per request
is open. Whether the boundaries are inclusive and what an empty range returns were not measured.

The response is an array; one element per delivery point:

| `deliverypointnumber` | `measurements[]` |
| --- | --- |
| 33-character string, the Swiss Zählpunkt | Array of measurement objects |
| Delivery point the following measurements belong to | Readings for one interval at the requested aggregation |

Interval boundaries of a measurement:

| `zeitstempel_von_utc` | `zeitstempel_bis_utc` |
| --- | --- |
| `dateTimeOnly`, UTC, no offset and no `Z` | `dateTimeOnly`, UTC, no offset and no `Z` |
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

For all three counters, `1` is normal operation — including `anzahl_linien_fw` and `anzahl_linien_fb`.
`0` is not the normal value and does not mean "no line". A value greater than `1` signals a data quality
problem, so such an interval should not be summed blindly.

A measurement element carries no field that says whether it is consumption or production; that
distinction comes from the meter's `type` for the same `deliverypointnumber`.

### `GET /energie/dynamische-preise`

Dynamic prices, with German field names. Parameters:

| `tariff_type` | `metering_code` | `start_timestamp` | `end_timestamp` |
| --- | --- | --- | --- |
| Tariff product selector | Metering code | Start of the requested range | End of the requested range |
| Chooses the tariff | Metering code the prices belong to | Default: start of the current day in CET | Default: end of the current day in CET |

The same data is also published **without authentication** at

```text
https://e-ckw-public-data.de-c1.eu1.cloudhub.io/api/v1/netzinformationen/energie/dynamische-preise
```

A measured day on the public feed returned 96 quarter-hour slots, each carrying `grid`, `electricity`,
`integrated` and `grid_usage` as arrays of one `{unit, value}` object with `unit: CHF_kWh`. That public
endpoint is what the companion integration `swiss-dynamic-tariffs` uses; this integration does not
consume the endpoint.

## Error responses

The deployed model attaches a documented body to every error response, so the cause can be told apart.

| Status | Documented body | Cause |
| --- | --- | --- |
| `400` | `{"error": "JWT Token is required."}` (live gate); `"Token was not provided."` (model) | Missing token. Every meter-host path answers this before routing runs, so an unknown path and a missing token look alike. |
| `401` | `"Bad or expired token. To fix, you should re-authenticate the user."` | Token is invalid or expired |
| `403` | `"The client id validation failed."` | Client id or its registration is wrong |
| `408` | `"Timeout to get customer data was exceeded."` | CKW's upstream customer lookup timed out |
| `429` | `{"error": "Quota has been exceeded"}` | Rate limit or quota exhausted |
| `500` | Generic service error | Unspecified server failure |
| `503` | `"Error communicating with JWKS server."` | Token verification cannot reach the key set |

The measurement endpoint adds a `400` whose body is
`"response too big, please adjust aggregation or duration accordingly"` when the requested range
exceeds the server cap. Since the unauthenticated gate runs before routing, a `404` cannot be observed
without a token.

## Rate limiting

The limit is enforced **per user**. Every response on the public host carries three headers:

| `x-ratelimit-limit` | `x-ratelimit-remaining` | `x-ratelimit-reset` |
| --- | --- | --- |
| Requests allowed per window | Requests still available in the current window | Milliseconds until a new window starts |

On the public host the measurement was `x-ratelimit-limit: 4`, `x-ratelimit-remaining: 3`,
`x-ratelimit-reset: 998` — four requests per roughly one-second window.

The meter host sends no `x-ratelimit-*` headers without a token, so its policy cannot be observed from
outside. Whether it shares the public host's 4-per-second limit is [open](#open-questions). `429` is
among the documented error responses, and a backfill large enough to hit the limit must be paced.

## Data availability and constraints

- Readings are available **only up to yesterday**, because the meter may legally be read once per day.
  Nothing more recent than the previous day is returned.
- There is **no real-time power value** and no live consumption figure: the API returns intervals, not a
  current reading.
- Amounts (`betrag_ht`, `betrag_nt`) are base prices: **without VAT and without surcharges**. A sum of
  them is not the invoice total.
- `deliverypointnumber` is exactly 33 characters — the Swiss Zählpunkt.
- `anzahl_linien_p`, `anzahl_linien_fw` and `anzahl_linien_fb` are normally `1`; above `1` signals a
  data quality problem.

## Open questions

These remained open after the 2026-11-01 measurements. None of them is answered by this page.

- **Auth registration:** the client id, scopes and exact PKCE parameters were not measured; the meter
  host is unreachable without a client id and a login. Whether a separate test or sandbox base URL
  exists is likewise unmeasured.
- **Response mode:** whether CKW can return `response_mode=query` to Home Assistant's callback, or
  insists on `fragment`, decides whether the pasted-code fallback (#50) is required.
- **`minute(s)` width:** does a `minute` or `minutes` request return 60 rows an hour, four rows, or the
  same payload as the other spelling?
- **Day boundaries:** where a daily bucket starts. The examples sit on quarter hours, not midnight, so
  the contract says nothing about it.
- **Range cap:** what is the maximum range per request before the `400` "response too big"?
- **Meter-host rate limit:** do the `x-ratelimit-*` headers appear on a `200` from the meter host, and
  with which limit?
- **Drifting price fields:** does a real account return `feed_in` or `grid_usage` in any object this
  integration consumes? Neither appears in the two endpoints we use.
- **Unmeasured field formats:** the exact types of location `id`, `gpnr`, the address fields, meter
  `id`, and `supply_start_date` / `supply_end_date`.
- **Aggregation spelling:** whether the singular and plural values differ for anything other than
  `minute`/`minutes`, and whether range boundaries are inclusive or what an empty range returns.
- **History:** whether older history is retained, how far back it reaches, and whether intervals are
  ever revised after delivery.
- **Support:** support hours, response times and a status page.

## Support

- Contact: **<openenergy@ckw.ch>**
- API access is requested through the form at **<https://www.ckw.ch/openenergy>** — free of charge, and
  required by Art. 8a–8d StromVV.
- A `403` because the client is missing an API audience or a role is a registration problem, not a code
  problem: escalate it to the address above, quoting the client id.

## Related

- [Measured API behaviour](ckw-openenergy-api-verification.md) — the evidence for every correction on
  this page.
- [Data model](../explanation/data-model.md) — why readings stop at yesterday, HT/NT, the quality flags,
  and why there is no live power sensor.
- [ADR-0001 — OAuth2 with PKCE vs. pasted code](../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md)
  — why the browser flow is the default and what the fallbacks cost.
- [ADR-0006 — measured auth constraints](../explanation/decisions/0006-auth-measured-constraints.md) —
  why only the browser flow is pursued.
- [Entities](entities.md) — the sensors and statistics built from these fields.
- [Configuration parameters](configuration.md) — what the user enters, and what is derived.
- [Glossary](glossary.md) — every German and API term above in one sentence.
