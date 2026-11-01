# Mock CKW OpenEnergy API

A local, offline stand-in for the CKW OpenEnergy API and its Keycloak login, so the
Home Assistant integration can be developed and tested without CKW credentials, SMS
or network access (#141, feeds #39 fixture mode).

> **This is a mock.** It serves committed fixtures, not CKW data. Its tokens are JWT
> *shaped* but unsigned (`"alg": "none"`, signature `mock-unsigned`) and **nothing can
> verify them** - so never treat a mock token as a credential, and never point a real
> client at it expecting a security boundary. What else is faithful and what is
> invented is listed under [Fidelity](#fidelity).

## Run it

```bash
mise run dev-mock                                    # http://0.0.0.0:8787
mise exec -- python -m scripts.dev.mock --port 8787  # same thing, explicit
mise exec -- python scripts/dev/mock/app.py --port 8787
```

Both the API and the fake Keycloak are served on that one port:

| URL | Purpose |
|---|---|
| `http://localhost:8787/api/v1/...` | OpenEnergy API |
| `http://localhost:8787/realms/CKW-prod/...` | fake Keycloak realm |

From inside the Podman dev container (Phase 1b) use
`http://host.containers.internal:8787` for both - the server binds `0.0.0.0` for that
reason. On a shared host, pass `--host 127.0.0.1` to keep it local.

There is no Home Assistant import anywhere in `scripts/dev/mock/`, and no outbound
request is ever made.

## Points to configure the integration at

| Setting | Value |
|---|---|
| API base URL | `http://host.containers.internal:8787/api/v1` |
| Authorization URL | `http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/auth` |
| Token URL | `http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/token` |
| OTP URL | `http://host.containers.internal:8787/realms/CKW-prod/otp/login` |
| Client id / secret | anything (`ckw-dev-mock`) - the mock never checks them |
| Redirect URI | your HA instance's `.../auth/external/callback` |

The auth endpoint has no login form: it consents instantly and redirects back with a
`code`. The token endpoint accepts `authorization_code` (a code issued by that
redirect, single use), `refresh_token` (any value) and `password` with a `code` (the
SMS/OTP variant, single use). Without a `code` the password grant is refused with
`403`, which is what the integration must turn into "ask the user for the SMS code".

## Endpoints

Every `/api/v1` path runs the JWT gate **before routing**, exactly like the real meter
host: without a bearer token (or with a non-bearer `Authorization` header) it answers
`400 {"error": "JWT Token is required."}` - a typo in a path looks like an auth
problem, and the response carries no `x-ratelimit-*` headers. A known path with an
invalid or expired token answers `401` with the measured body. With a valid token, an
unknown path is a JSON `404`.

```bash
TOKEN=anything
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8787/api/v1/smartmeter/customer
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8787/api/v1/smartmeter/measurement/20260922/20261031/day
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8787/api/v1/energie/dynamische-preise
```

* `GET /api/v1/smartmeter/customer` - `tests/fixtures/customer.json`: the deployed
  shape `customer.User[].locations[].meters[]`, one Lucerne location and three meters
  (electricity import, PV export, gas consumption) with the documented enums
  `model` ∈ {`SMART`, `ANALOGUE`}, `type` ∈ {`import`, `export`, `consumption`,
  `production`}, `fuel` ∈ {`elec`, `gas`}. One meter carries `custom.meter_type_indicator`,
  and both the customer and a meter carry an extra key the contract never mentions -
  the shapes are open (`shacl:closed: false`), so a client must tolerate them.
* `GET /api/v1/smartmeter/measurement/{von}/{bis}/{aggregation}` - a JSON array with
  one element per delivery point, `{deliverypointnumber, measurements: [...]}`. The
  meters are **not** filtered by delivery point, exactly like the real endpoint.
  `von`/`bis` are `YYYYMMDD` and inclusive or a `400` is returned. `aggregation` must
  be one of the ten documented values - `year`, `years`, `month`, `months`, `week`,
  `weeks`, `day`, `days`, `minute`, `minutes`; anything else, including a `day(s)`
  spelling, is a `400`. The aggregation picks the interval width:
  * `minute` / `minutes` - 15-minute rows, CKW's finest documented resolution, from
    `tests/fixtures/measurements_minutes.json` (one day, 2026-10-01).
  * `day` / `days` - daily rows from `tests/fixtures/measurements_days.json`.
  * `week(s)`, `month(s)`, `year(s)` - the daily rows of the requested range summed
    per ISO week, calendar month or calendar year: quantities and CHF costs are
    summed, `anzahl_linien_p/fw/fb` take the maximum.
  * A range longer than 400 days (or `?range=too-big`) answers
    `400 {"error": "response too big, please adjust aggregation or duration accordingly"}`.
* `GET /api/v1/energie/dynamische-preise` - 24 deterministic hourly entries for
  2026-11-01 with plausible CHF/kWh values. The real price feed is quarter-hour and
  carries `{unit, value}` objects; the mock picks a readable shape instead:
  `{day, prices: [{zeitstempel_von_utc, zeitstempel_bis_utc, preis_chf_kwh}]}`.

Every response that gets past the JWT gate - including errors and redirects from the
fake IdP - carries rate-limit headers matching the measured public host:
`x-ratelimit-limit: 4`, an `x-ratelimit-remaining` that counts down within the window,
and `x-ratelimit-reset` in milliseconds.

## Error injection cheat sheet

Query parameters work on **any** endpoint:

| Parameter | Effect |
|---|---|
| `?fail=401` | `401 {"error": "Bad or expired token. To fix, you should re-authenticate the user."}` |
| `?fail=403` | `403 {"error": "The client id validation failed."}` |
| `?fail=408` | `408 {"error": "Timeout to get customer data was exceeded."}` |
| `?fail=429` or `?fail=quota` | `429 {"error": "Quota has been exceeded"}` plus rate-limit headers |
| `?fail=500` | `500` with a generic `{"error": ...}` body |
| `?fail=503` | `503 {"error": "Error communicating with JWKS server."}` |
| `?fail=418` | `400` - only the statuses above are injectable |
| `?range=too-big` | `400` "response too big ..." on the measurement endpoint |
| `?slow=2.5` | sleep 2.5 s before answering (timeout tests) |
| `?ratelimit-remaining=0&ratelimit-reset=2000` | override the reported quota |
| `?ratelimit-remaining=0&ratelimit-reset=2000&fail=429` | the "quota exhausted" response |

The parameters only affect the one request they are sent with; nothing is stored.

## Fixtures

`tests/fixtures/customer.json` is hand-written and obviously fake: customer
`0000000099`, user `Max Beispiel`, `Musterweg 1`, `6000 Luzern`, and 33-character
delivery point numbers in the real `CH1003601234500000000000000000000` style.

`tests/fixtures/measurements_days.json` is generated, never hand-edited: 40
consecutive days (2026-09-22 .. 2026-10-31, ending before "today"), one entry per day
per meter, 10-25 kWh/day split roughly 60/40 into HT/NT at 0.24/0.19 CHF per kWh - the
base price without VAT and surcharges, as the contract says. Day boundaries are
Europe/Zurich midnights in UTC, so 2026-10-25 - the day daylight saving ends - spans
25 hours. Exactly one entry (the import meter on 2026-10-02) has `anzahl_linien_p: 2`
to exercise the quality-flag path; everything else is `1`, and, as the deployed model
shows, `anzahl_linien_fw`/`anzahl_linien_fb` are `1` too. The file keys the series by
delivery point because the measurement objects themselves carry no meter field.

`tests/fixtures/measurements_minutes.json` is generated as well: one day
(2026-10-01) in 96 quarter-hour intervals per meter. HT follows the Swiss tariff
window (Mon-Fri 07:00-20:00, Sat 07:00-13:00, Sun never), so the HT/NT split comes
from the clock rather than a fixed share. All line counters are `1`.

Rebuild both from their fixed seeds:

```bash
mise exec -- python scripts/dev/mock/generate_fixtures.py
```

The output is byte-identical on every run, so a diff means someone changed the
generator on purpose.

## Fidelity

Faithful to `docs/reference/ckw-openenergy-api-verification.md`:

* paths and the pre-routing JWT gate (`400` without a token, unknown paths included);
* the documented error bodies, byte for byte, as single-key `{"error": ...}` JSON;
* the meter enums (`SMART`/`ANALOGUE`, `import`/`export`/`consumption`/`production`,
  `elec`/`gas`), the nested `customer.User[].locations[]` shape, the open shapes with
  `custom` and unknown keys, and 33-character delivery point numbers;
* `anzahl_linien_fw`/`_fb` = 1 normally, and a quality flag as `_p` > 1;
* the aggregation enum and the interval widths: `minute(s)` is 15 minutes, `day(s)` is
  one local day, anything coarser aggregates the daily rows;
* `x-ratelimit-limit: 4`, counting-down remaining, millisecond reset, and the range-cap
  error message.

Still invented, and not to be read as CKW behaviour:

* every value: quantities, prices, the gas meter's readings and the exact meter
  identity, address and customer number;
* the `500` body text (the model only says "generic service error") and the token
  values the mock treats as invalid/expired - a real server validates a signature;
* the 400-day range cap (the message is measured, the threshold is not);
* week/month/year bucketing (ISO weeks starting Monday; sums and maxima are our
  reading of "aggregation"), and the fact that a range outside the fixture simply
  returns empty measurement lists;
* the fake Keycloak, the dynamic-price shape, and the fact that the rate limiter only
  *reports* the quota - it does not reject the fifth request, so a local test run
  cannot lock itself out. The real host presumably answers `429`.

## Tests

```bash
mise exec -- python -m pytest tests/test_mock_api.py -q
```

The tests start the app with `aiohttp.test_utils.TestServer`/`TestClient` on an
ephemeral localhost port; `pytest-aiohttp` is not needed.
