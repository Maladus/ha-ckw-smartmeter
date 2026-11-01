# Mock CKW OpenEnergy API

A local, offline stand-in for the CKW OpenEnergy API and its Keycloak login, so the
Home Assistant integration can be developed and tested without CKW credentials, SMS
or network access (#141, feeds #39 fixture mode).

> **This is a mock.** It serves committed fixtures, not CKW data. It does not
> reproduce CKW behaviour, rate limits, error semantics or token lifetimes beyond
> what the integration needs. Its tokens are JWT *shaped* but unsigned
> (`"alg": "none"`, signature `mock-unsigned`) and **nothing can verify them** - so
> never treat a mock token as a credential, and never point a real client at it
> expecting a security boundary.

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

All three need `Authorization: Bearer <token>` (any non-empty token works) and answer
`401` with a JSON body otherwise.

```bash
TOKEN=anything
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8787/api/v1/smartmeter/customer
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8787/api/v1/smartmeter/measurement/20260922/20261031/day
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8787/api/v1/energie/dynamische-preise
```

* `GET /api/v1/smartmeter/customer` - `tests/fixtures/customer.json`: one customer,
  one Lucerne location, an electricity meter and a PV feed-in meter.
* `GET /api/v1/smartmeter/measurement/{von}/{bis}/{aggregation}` - a slice of
  `tests/fixtures/measurements_days.json`: a JSON array with one element per delivery
  point, `{deliverypointnumber, measurements: [...]}`, matching
  `docs/reference/ckw-openenergy-api.md`. The meters are **not** filtered by delivery
  point, exactly like the real endpoint. `von`/`bis` are `YYYYMMDD` and inclusive or a
  `400` is returned. `aggregation` must be one of the ten documented values - `year`,
  `years`, `month`, `months`, `week`, `weeks`, `day`, `days`, `minute`, `minutes`;
  anything else, including a `day(s)` spelling, is a `400`.
* `GET /api/v1/energie/dynamische-preise` - 24 deterministic hourly entries for
  2026-11-01 with plausible CHF/kWh values. The contract does not state this shape, so
  the mock picks a readable one:
  `{day, prices: [{zeitstempel_von_utc, zeitstempel_bis_utc, preis_chf_kwh}]}`.

The customer and measurement shapes follow `docs/reference/ckw-openenergy-api.md`; the
fields the contract leaves open (`Unknown — verify with CKW`) are the mock's own
plausible values, and the dynamic-price shape is a guess. Re-check that page before
trusting a field here.

Every response - including errors and redirects from the fake IdP - carries
`x-ratelimit-limit: 100`, `x-ratelimit-remaining` and `x-ratelimit-reset`.

## Error injection cheat sheet

Query parameters work on **any** endpoint:

| Parameter | Effect |
|---|---|
| `?fail=401\|403\|408\|429\|500\|503` | answer that status with a JSON error body, before the handler runs |
| `?fail=418` | `400` - only the statuses above are injectable |
| `?slow=2.5` | sleep 2.5 s before answering (timeout tests) |
| `?ratelimit-remaining=0&ratelimit-reset=2000` | report a spent quota in the headers while still answering `200` |
| `?ratelimit-remaining=0&ratelimit-reset=2000&fail=429` | the "quota exhausted" response |

The parameters only affect the one request they are sent with; nothing is stored.

## Fixtures

`tests/fixtures/customer.json` is hand-written and obviously fake: delivery point
numbers `CH000...0001` / `CH000...0002`, `Musterweg 1`, `6000 Luzern`.

`tests/fixtures/measurements_days.json` is generated, never hand-edited: 40
consecutive days (2026-09-22 .. 2026-10-31, ending before "today"), one entry per day
per meter, 10-25 kWh/day split roughly 60/40 into HT/NT at 0.24/0.19 CHF per kWh - the
base price without VAT and surcharges, as the contract says. Day boundaries are
Europe/Zurich midnights in UTC, so 2026-10-25 - the day daylight saving ends - spans
25 hours. Exactly one entry (the electricity meter on 2026-10-02) has
`anzahl_linien_p: 2` to exercise the quality-flag path; everything else is `1`, and
`anzahl_linien_fw`/`anzahl_linien_fb` stay `0`. The file keys the series by delivery
point because the measurement objects themselves carry no meter field.

Rebuild it from its fixed seed:

```bash
mise exec -- python scripts/dev/mock/generate_fixtures.py
```

The output is byte-identical on every run, so a diff means someone changed the
generator on purpose.

## Tests

```bash
mise exec -- python -m pytest tests/test_mock_api.py -q
```

The tests start the app with `aiohttp.test_utils.TestServer`/`TestClient` on an
ephemeral localhost port; `pytest-aiohttp` is not needed.
