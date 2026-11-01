# CKW OpenEnergy API: measured behaviour

This page answers: what did we measure against the live CKW endpoints and the deployed API model, and
where does that contradict [the contract transcription](ckw-openenergy-api.md)?

Everything below was measured on 2026-11-01 with `curl` and a Python probe. Each claim names the URL
that produced it, so a later reader can re-run the probe and see whether CKW changed something. The
transcribed contract stays the lookup page for field names; this page records the disagreements.

## Where the evidence came from

| Source | URL | Auth | What it gave |
| --- | --- | --- | --- |
| Published portal page | `https://eu1.anypoint.mulesoft.com/exchange/portals/axpo/e8a7c8ac-9233-49e5-9623-ae7dbe3b6868/e-customer-smartmeter/` | none | The prose contract: two endpoints, login flow, versioning, rate limiting. |
| Deployed API model | `https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/console/?amf` | none | The full RAML 1.0 model of the running app, 7,513 bytes, AMF model version 3.8.2. The API console loads it in the browser; nothing stops a script from fetching it. |
| Public data API | `https://e-ckw-public-data.de-c1.eu1.cloudhub.io/api/v1/netzinformationen/energie/dynamische-preise` | none | Live dynamic prices, 200, 26 KB. |
| Meter API | `https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1/…` | JWT | Not reachable without a client id and a login. See the open questions. |
| Identity provider | `https://id.ckw.ch/realms/CKW-prod/.well-known/openid-configuration`, `…/CKW-dev/…` | none | Both realms answer 200. PKCE `S256` supported, `offline_access` offered, `password` grant advertised by the realm. |
| Local mock | `mise run dev-mock`, port 8787 | mock token | Divergences listed under [the mock](#the-local-mock-diverges). |

The `?amf` endpoint is the important one. It returns the same contract CKW publishes, but as data
instead of prose, so field names, enums and examples can be read without interpretation.

## Resolution: quarter hour, not minute

Every measurement example in the deployed model is exactly 15 minutes wide:

```text
2020-09-25T16:30:00 → 2020-09-25T16:45:00   Δ 0:15:00   menge_fakturiert_ht 13.968 kWh
2020-11-24T21:30:00 → 2020-11-24T21:45:00   Δ 0:15:00   menge_fakturiert_nt  5.704 kWh
```

The aggregation enum holds all ten values (`year`, `years`, `month`, `months`, `week`, `weeks`, `day`,
`days`, `minute`, `minutes`), so a finer request is accepted. What the model shows is that CKW's own
sample data is quarter-hour. No 1-minute example exists anywhere in it, and the other metered time
series on the same platform is also quarter-hour: the public price feed returns 96 slots per day.

Read `minute` and `minutes` as "finest available", not as "one minute". Whether the server caps the
request at 15 minutes, or returns 60 rows per hour with 15 of them populated, needs one authenticated
call. The difference matters for `statistics.py`, not for the Energy dashboard.

## No phase dimension

This one is settled. The measurement node shape in the deployed model declares exactly nine
properties:

| Field | CKW's own description |
| --- | --- |
| `zeitstempel_von_utc`, `zeitstempel_bis_utc` | `dateTimeOnly`, no offset, no `Z` |
| `betrag_ht` | Amount for high tariff product in francs. |
| `betrag_nt` | Amount for low tariff product in francs. |
| `menge_fakturiert_ht` | Billed amount for high tariff product in KWh. |
| `menge_fakturiert_nt` | Billed amount for low tariff product in KWh. |
| `anzahl_linien_p` | Number of physical timeseries (value > 1 usually means data quality issue). |
| `anzahl_linien_fw` | Number of billable timeseries (value > 1 usually means data quality issue). |
| `anzahl_linien_fb` | Number of timeseries for blind current (value > 1 usually means data quality issue). |

No phase, no conductor, no per-line value. A three-phase meter arrives as one summed number plus a
counter. CKW has an idiom for multi-valued readings, an array of `{unit, value}` objects, and the price
endpoint uses it. Measurements use plain scalars, which is what a designer would do only if there were
nothing to enumerate.

## Discrepancies with the transcription

Ordered by the damage they would do if believed.

1. **The customer response nests a `User` array above `locations`.** The documented shape is
   `customer.User[].locations[].meters[]`, and `User` carries `id`, `name`, `email`, `phone`. A client
   written against `customer.locations` raises `KeyError` on the first live call. The payload also
   contains an email address and a phone number, so `diagnostics.py` must redact two fields nobody
   listed before.
2. **`anzahl_linien_fw` and `anzahl_linien_fb` are `1` normally, not `0`.** The deployed example has
   `p=1, fw=1, fb=1`. Our fixture generator sets `fw: 0, fb: 0` and calls them "non-electricity line
   counts". A `statistics.py` that skips intervals where a counter deviates from 1, as specified,
   would skip every interval of every real meter.
3. **Meter enums are documented, not unknown.** `model` ∈ {`SMART`, `ANALOGUE`}, `type` ∈ {`import`,
   `export`, `consumption`, `production`}, `fuel` ∈ {`elec`, `gas`}. So feed-in can be identified
   (`type=export` or `production`) and gas can be filtered out (`fuel=elec`) without guessing. Our
   fixture uses `type: electricity`, `fuel: electricity`, `model: e450`, none of which the API emits.
4. **Error bodies are documented and tell the causes apart.** The transcription says no body schema
   exists and the causes cannot be distinguished. The deployed model attaches an example to every
   error response:
   - `400` "Token was not provided."
   - `401` "Bad or expired token. To fix, you should re-authenticate the user."
   - `403` "The client id validation failed."
   - `408` "Timeout to get customer data was exceeded."
   - `429` `{"error": "Quota has been exceeded"}`
   - `500` generic service error
   - `503` "Error communicating with JWKS server."
   - the measurement `400` also carries "response too big, please adjust aggregation or duration
     accordingly", which is the range cap the transcription lists as unknown.

   Error mapping can be exact. The missing token really does answer `400`, not `401`, which the live
   probe confirmed: `{"error": "JWT Token is required."}`.
5. **Password grant is refused.** The portal states `grant_type:authorization_code (grant type
   password is not supported)` even though the realm metadata still advertises `password`. The headless
   OTP flow in [ADR-0001](../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md) has no
   implementation path. The documented authorize URL also asks for `response_mode=fragment`, which puts
   the code in the URL fragment where Home Assistant's callback will not find it.
6. **Fields appear without a version bump.** The portal says "Completely new fields might also be added
   in the same major version", and it already happened: the model describes a price object with
   `feed_in`, production returns `grid_usage` instead. Parsers must tolerate unknown keys and missing
   ones. The customer and measurement shapes are declared open (`shacl:closed: false`), so extra keys
   are legal everywhere.
7. **Meters carry a `custom` object.** It holds free-text `meter_type_indicator`. Harmless, but a client
   that validates strictly will reject it.
8. **Dynamic prices take parameters.** `tariff_type`, `metering_code`, `start_timestamp`,
   `end_timestamp`, the timestamps defaulting to the start and end of the current day in CET. The
   transcription says no parameters are documented. Not our endpoint, but the page is wrong.

Two things the deployed model confirms rather than corrects. Prices are "base price (not including vat
and other surcharges)", and "Data is available until yesterday, since reading out of the meter is
legally only allowed once per day". Also confirmed: `deliverypointnumber` is 33 characters with the
shape `CH1003601234500000000000000000000`, and the rate-limit headers mean max requests per window,
remaining quota, and milliseconds until the next window.

## The unauthenticated JWT gate

Every path on the meter host answers the same `400` before routing runs. Probed on 2026-11-01:

| Path | Response |
| --- | --- |
| `/api/v1` | `400 {"error": "JWT Token is required."}` |
| `/api/v1/smartmeter/customer` | same |
| `/api/v1/smartmeter/measurement/20261030/20261031/days` | same |
| `/api/v1/openapi.json` | same |
| `/api/v1/nonsense` | same |

Two consequences. A `404` is unreachable without a token, so a typo in a path looks like an auth
problem. And the `400` carries no `x-ratelimit-*` headers, while the public host sends them on every
response, so the customer API's rate limiting cannot be observed from outside.

The public host, for comparison, advertised `x-ratelimit-limit: 4`, `x-ratelimit-remaining: 3`,
`x-ratelimit-reset: 998`. Four requests per roughly one-second window. If the meter host shares that
policy, a 730-day backfill has to be paced, not looped.

## What the public price feed showed

96 slots for 2026-11-01, `00:00+01:00` to `00:00+01:00` the next day, with `publication_timestamp`
the day before. Each slot carries `grid`, `electricity`, `integrated` and `grid_usage`, each an array
of one `{unit, value}` with `unit: CHF_kWh`. Timestamps are timezone-aware there, unlike the bare
`dateTimeOnly` in the meter contract, so the missing offset is a property of the smartmeter API, not of
the platform.

The interesting part: 24 distinct values, each repeated across its four quarter-hour slots. CKW
upsamples one hourly price into four 15-minute rows. That is a precedent for how this platform treats
finer resolutions than its source data.

## The local mock diverges

`scripts/dev/mock/` is a stub, and the divergences sit exactly where the contract is interesting.

| Behaviour | Mock | Meter API |
| --- | --- | --- |
| Missing token | `401` | `400 {"error": "JWT Token is required."}` |
| Unknown path | JSON `404` | `400`, same body as everything else |
| `aggregation` | ignored, always daily rows | decides the interval width |
| `minute(s)` vs `day(s)` | identical payload | unknown, probably 15 minutes |
| Meter `model` / `type` / `fuel` | `e450` / `electricity` / `electricity` | `SMART` / `import` / `elec` |
| `anzahl_linien_fw`, `_fb` | `0` | `1` |
| Customer shape | `customer.locations[]` | `customer.User[].locations[]` |

`tests/test_mock_api.py` asserts the `401`, so the suite currently locks in behaviour the real API
contradicts. Fixtures are only useful if they are wrong in ways the client is meant to tolerate, not
wrong in ways it will meet on the first call.

## Open questions

These need one authenticated request each, so they stay open until somebody logs in with a real client
id.

- Does `minute` return 60 rows an hour, 4, or the same as `minutes`? Read the interval widths.
- Where does a daily bucket start? The example timestamps sit on a quarter hour (`16:30`), not on a
  midnight, so the examples say nothing about day boundaries. Bucketing in `Europe/Zurich` remains an
  inference from the legal "read once per day" note.
- What is the maximum range per request before `400` "response too big"? The message exists, the
  threshold does not.
- Do the `x-ratelimit-*` headers appear on a `200` from the meter host, and with which limit?
- Does a real account return `feed_in` or `grid_usage` in any object we consume? Neither appears in the
  two endpoints we use, but the drift proves the question is worth asking.

## Reproducing

```bash
# the deployed model, no auth needed
curl -sS --compressed "https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/console/?amf" -o amf.json

# the public price feed and its rate-limit headers
curl -sS -D- -o /dev/null \
  "https://e-ckw-public-data.de-c1.eu1.cloudhub.io/api/v1/netzinformationen/energie/dynamische-preise"

# the unauthenticated gate
curl -sS -o- "https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/api/v1/smartmeter/customer"
```

To read the enums and the example intervals out of the model, without trusting this page:

```python
import gzip
import json
import urllib.request

URL = "https://e-customer-smartmeter.de-c1.eu1.cloudhub.io/console/?amf"
raw = urllib.request.urlopen(URL).read()
if raw[:2] == b"\x1f\x8b":
    raw = gzip.decompress(raw)
model = json.loads(raw)
graph = {node["@id"]: node for node in model["@graph"]}

# every enum the model declares, named after the property that uses it
for node in model["@graph"]:
    shape = node.get("shacl:in")
    if not shape:
        continue
    values, ref_id = [], shape["@id"]
    while ref_id in graph and graph[ref_id].get("@type") == "rdfs:Seq":
        ref = graph[ref_id]
        values += [graph[ref[k]["@id"]].get("data:value")
                   for k in sorted((k for k in ref if k.startswith("rdfs:_")),
                                  key=lambda s: int(s.split("_")[1]))]
        ref_id = ref_id + "/next"
    print(node.get("shacl:name"), "=", values)

# interval widths in the documented measurement example
for node in model["@graph"]:
    props = {k.split(":", 1)[1]: graph[v["@id"]].get("data:value")
             for k, v in node.items() if k.startswith("data:") and isinstance(v, dict)}
    if "zeitstempel_von_utc" in props:
        print(props["zeitstempel_von_utc"], "->", props["zeitstempel_bis_utc"],
              "| lines p/fw/fb", props["anzahl_linien_p"],
              props["anzahl_linien_fw"], props["anzahl_linien_fb"])
```

That prints, on 2026-11-01:

```text
type = ['year', 'years', 'month', 'months', 'week', 'weeks', 'day', 'days', 'minute', 'minutes']
schema = ['grid', 'electricity', 'feed_in', 'integrated']
model = ['SMART', 'ANALOGUE']
type = ['import', 'export', 'consumption', 'production']
fuel = ['elec', 'gas']
2020-09-25T16:30:00 -> 2020-09-25T16:45:00 | lines p/fw/fb 1 1 1
2020-11-24T21:30:00 -> 2020-11-24T21:45:00 | lines p/fw/fb 1 1 1
```

## Related

- [ckw-openenergy-api.md](ckw-openenergy-api.md): the transcribed contract these findings are measured
  against.
- [data-model.md](../explanation/data-model.md): what the fields mean, and why readings stop at
  yesterday.
- [ADR-0001](../explanation/decisions/0001-auth-oauth2-vs-pasted-code.md): the login decision that the
  refused password grant affects.
- [glossary.md](glossary.md): HT/NT, billed quantity, Zählpunkt.
- [PLAN.md](../../PLAN.md) tasks #5, #6 and #7: the live verification, fixture capture and meter
  topology checks these findings unblock.
