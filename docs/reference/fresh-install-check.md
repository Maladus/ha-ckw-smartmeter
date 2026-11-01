# Fresh-install verification

This page answers: does a stranger's install of this integration on a clean Home Assistant instance
work end to end, what exactly was observed, and what does a new user see?

The record below is one acceptance run on 2026-11-01 (#107). It is the evidence behind the ✅ on the
`install.md` row in [PLAN.md](../../PLAN.md) and behind the release decision: the published repository
was cloned into an empty Home Assistant, onboarded over the API, pointed at the local mock, and driven
through the whole config flow — application credentials, fake Keycloak login, meter picker, entry,
entities, long-term statistics, diagnostics.

Every value quoted here comes from the mock's committed fixtures: customer `0000000099`,
`Musterweg 1`, Luzern, and the obviously fake delivery points `CH1003601234500000000000000000000`
(import) and `…0001` (export). No real customer data, token or password is in this page. The delivery
points are quoted in their masked form (`…0000`, `…0001`) wherever a transcript allows it.

## What was verified, and against what

| Piece | Version / value |
| --- | --- |
| Repository | `https://github.com/Maladus/ha-ckw-smartmeter`, public, `main` at `7ae2b16` (`docs: repository is public and branch-protected (#110)`) |
| Integration | `custom_components/ckw`, `manifest.json` version `0.1.0`, copied from that commit — not a bind mount of a working tree |
| Home Assistant | image `ghcr.io/home-assistant/home-assistant:stable`, running `2026.10.4` on Python 3.14.9 |
| Container | `ha-fresh`, rootless Podman 4.9.3, port `8125`, config dir `/tmp/ha-fresh` (empty at the start), no mount from this repository |
| Mock | `mise run dev-mock`, OpenEnergy API and fake Keycloak on `0.0.0.0:8787`, fixtures `2026-09-22 .. 2026-10-31` |
| Instance clock | 2026-11-01, `TZ=Europe/Zurich` in the container; Home Assistant's own timezone is still `UTC` because the API onboarding leaves it unset, which is why the integration does its day arithmetic in `Europe/Zurich` on its own (ADR-0004) |

## How the instance was built

```bash
mkdir -p /tmp/ha-fresh
podman run -d --name ha-fresh \
  -p 8125:8123 \
  -v /tmp/ha-fresh:/config:Z \
  -e TZ=Europe/Zurich \
  -e CKW_API_BASE_URL=http://host.containers.internal:8787/api/v1 \
  -e CKW_AUTHORIZE_URL=http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/auth \
  -e CKW_TOKEN_URL=http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/token \
  --add-host=host.containers.internal:host-gateway \
  ghcr.io/home-assistant/home-assistant:stable
```

The first start created the stock configuration in the empty config dir; nothing from this
repository's `config/` or from `ha-dev` was reused. `/manifest.json` answered `200` after about 25 s.

Two container-side notes, both unrelated to the integration:

- Home Assistant's frontend listens on `8123` inside the image, so `-p 8125:8123` is the mapping. The
  port is only the host side; every URL below uses `localhost:8125`.
- `ERROR (SyncWorker_*) [aiodhcpwatcher] Cannot watch for dhcp packets: [Errno 1] Operation not
  permitted` appears on every boot. A rootless container may not open a DHCP socket; the same line
  appears on `ha-dev`.

## Step 1 — install the way the README says

[How-to: install](../how-to/install.md) offers the manual route for today (the HACS route needs a
release, #109). The manual route is "download the repository, copy the folder
`custom_components/ckw` into `/config/custom_components/`":

```bash
git clone --depth 1 https://github.com/Maladus/ha-ckw-smartmeter /tmp/ha-ckw-clone
cp -a /tmp/ha-ckw-clone/custom_components/ckw /tmp/ha-fresh/custom_components/ckw
podman restart ha-fresh
```

Cloning straight into `/tmp/ha-fresh/custom_components/ckw` would put `manifest.json` one level too
deep (`custom_components/ckw/custom_components/ckw/manifest.json`) and Home Assistant would not load
it, which is why the clone lands beside the instance and the folder is copied — exactly what
"Code → Download ZIP, then copy the folder" does in the browser.

Home Assistant found and parsed the component on the next start:

```text
2026-11-01 21:28:10.332 WARNING (SyncWorker_0) [homeassistant.loader] We found a custom integration
ckw which has not been tested by Home Assistant. This component might cause stability problems, be
sure to disable it if you experience issues with Home Assistant
```

That WARNING is Home Assistant's standing notice for every custom integration, not an error. The
integration also shows up where a user looks for it — the "Add integration" list accepts the handler:

```text
POST /api/config/config_entries/flow {"handler":"ckw"}
HTTP 200
{"type":"external","flow_id":"01M6CCRZWTEB4Z24T94P22VK94","handler":"ckw",
 "url":"http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/auth?…",
 "step_id":"auth"}
```

`GET /manifest.json` returning `200` only proves the frontend is up: that file is the PWA manifest and
never lists integrations. `manifest/list` is not the check either — it returns the 130 platform
integrations and no custom component, because `ckw` offers no platform to add in the UI. The two
checks that mean something are the loader WARNING above and the config flow accepting `ckw` as a
handler; both are quoted here.

## Step 2 — point the instance at the mock

The three `CKW_*` variables from `scripts/dev/.env.example` went into the container environment, as
above. From inside the container the mock is `host.containers.internal:8787`; the integration reads
those variables in `application_credentials.py` and names the pair it uses at debug level, here with
`custom_components.ckw: debug` while a second flow was started:

```text
21:30:33 DEBUG (MainThread) [custom_components.ckw.application_credentials] using development
override OAuth endpoints: http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/auth,
http://host.containers.internal:8787/realms/CKW-prod/protocol/openid-connect/token
```

At the stock WARNING level that line does not appear; the effect is visible only in the flow, which
then talks to the mock instead of `id.ckw.ch`.

## Step 3 — onboarding, over the API

The four onboarding steps, driven the way `scripts/dev/onboard.sh` drives them for `ha-dev`, against
port 8125 instead of 8123:

```text
GET /api/onboarding -> [{"step":"user","done":false},{"step":"core_config","done":false},
                        {"step":"analytics","done":false},{"step":"integration","done":false}]
POST /api/onboarding/users -> auth_code 04728a7a...
POST /auth/token -> access token (183 chars) stored in /tmp/ha-fresh-token
POST /api/onboarding/core_config -> 200
POST /api/onboarding/analytics -> 200
POST /api/onboarding/integration -> 200
GET /api/onboarding -> [{"step":"user","done":true},{"step":"core_config","done":true},
                        {"step":"analytics","done":true},{"step":"integration","done":true}]
```

## Step 4 — add the integration end to end

### 4a. Application credentials

Settings → Devices & services → Application credentials → CKW Smart Meter, client id
`ckw-dev-mock`, secret empty. The same thing over the websocket API:

```text
{"type":"application_credentials/create","domain":"ckw","client_id":"ckw-dev-mock","client_secret":""}
-> {"id":"ckw_ckw_dev_mock","domain":"ckw","client_id":"ckw-dev-mock","client_secret":""}
```

### 4b. Start the flow

Settings → Devices & services → Add integration → CKW Smart Meter. The flow goes straight to the
external step because exactly one credential exists:

```text
type: external | step: auth | flow_id: 01M6CCRZWTEB4Z24T94P22VK94
authorize URL: http://…/realms/CKW-prod/protocol/openid-connect/auth
               ?response_type=code&client_id=ckw-dev-mock
               &redirect_uri=https://my.home-assistant.io/redirect/oauth&state=…&code_challenge=…
               &code_challenge_method=S256&scope=openid+offline_access
```

`redirect_uri` is `https://my.home-assistant.io/redirect/oauth`, not the instance URL: `my` is part of
`default_config`, and Home Assistant hands out the My-Home-Assistant redirector whenever that
integration is loaded (`helpers/config_entry_oauth2_flow.async_get_redirect_uri`). This is production
behaviour, not a mock artefact.

### 4c. The browser login, and where a headless run stops

The fake Keycloak has no login form: it consents immediately and answers `302` to
`https://my.home-assistant.io/redirect/oauth?code=…&state=…`. That is where the headless run stops.
The redirector is a JavaScript page — title "Link account to Home Assistant?", buttons **Decline** and
**Link account** — and a human must click **Link account** (and, on first use, pick the instance,
because a local instance has no My-Home-Assistant cloud link). It then appends `code` and `state` to
`<instance>/auth/external/callback`.

The verification replaced that one click with the request the redirector makes, which is the whole
difference between the two runs:

```bash
# 1. the browser fetches the authorize URL; host.containers.internal is rewritten to
#    localhost because this browser lives on the host, inside the container the name resolves
curl -s -D headers.txt "http://localhost:8787/realms/CKW-prod/protocol/openid-connect/auth?…"
#    -> 302 Location: https://my.home-assistant.io/redirect/oauth?code=s7kdvIHW-…&state=eyJhbGci…
# 2. instead of my.home-assistant.io, call the instance callback directly
curl -s "http://localhost:8125/auth/external/callback?code=s7kdvIHW-…&state=eyJhbGci…"
#    -> 200, <script>window.close()</script>
```

Everything before and after that click is real: the PKCE authorize request, the authorization code, the
token exchange at the mock's token endpoint, and the customer probe the flow runs on the new token.
Nothing was faked and no flow state was written by hand.

### 4d. The meter picker, and the entry

The probe found two electricity meters, so the flow asked which one to read; the gas meter was filtered
out before the user saw the list:

```text
{"type":"form","handler":"ckw","step_id":"meter","description_placeholders":{"count":"2"},
 "data_schema":[{"name":"meter","required":true,"default":"CH1003601234500000000000000000000",
   "selector":{"select":{"options":[
     {"value":"CH1003601234500000000000000000000","label":"CH1003601234500000000000000000000 — Luzern"},
     {"value":"CH1003601234500000000000000000001","label":"CH1003601234500000000000000000001 — Luzern"}]}}}]}
```

Picking the import meter created the entry:

```text
HTTP 200
type: create_entry | title: 0000000099 — Luzern | entry_id: 01M6CCS38X7CKJ3TR4KSZY2GKS | state: loaded
```

The title is the entry title decided in ADR-0008, the masked meter names are the device names.

## Step 5 — what the instance then did

### 5a. Entities

Seven entities per delivery point, four of them enabled by default — the same shape
[reference: entities](entities.md) documents. Two devices were created, not one: the entry follows
every electricity meter the API returns, so the export meter `…0001` got its own device even though the
entry was configured for `…0000` (#134).

```text
sensor.smart_0000_consumption_total          '108.0'                        kWh
sensor.smart_0000_consumption_yesterday      '15.3'                         kWh
sensor.smart_0000_last_published_interval    '2026-10-31T23:00:00+00:00'
sensor.smart_0000_last_update                '2026-11-01T20:28:24+00:00'
sensor.smart_0001_consumption_total          '123.3'                        kWh
sensor.smart_0001_consumption_yesterday      '16.5'                         kWh
sensor.smart_0001_last_published_interval    '2026-10-31T23:00:00+00:00'
sensor.smart_0001_last_update                '2026-11-01T20:28:24+00:00'
```

The disabled ones are `consumption_high_tariff`, `consumption_low_tariff` and `quarter_hour_consumption`
on both devices, as [reference: entities](entities.md) says.

### 5b. The values are the fixture values

`consumption_yesterday` is the newest complete published day and `consumption_total` is the sum over the
rolling window the coordinator polls, both straight out of `tests/fixtures/measurements_days.json`:

| Sensor | State | Fixture arithmetic |
| --- | --- | --- |
| `sensor.smart_0000_consumption_yesterday` | `15.3` | last day of `…0000`, HT 8.6 + NT 6.7 |
| `sensor.smart_0000_consumption_total` | `108.0` | last seven days of `…0000` |
| `sensor.smart_0001_consumption_yesterday` | `16.5` | last day of `…0001` |
| `sensor.smart_0001_consumption_total` | `123.3` | last seven days of `…0001` |
| `sensor.smart_0000_last_published_interval` | `2026-10-31T23:00:00+00:00` | `zeitstempel_bis_utc` of the newest interval = 2026-11-01 local midnight |

The publication lag is real and in range: `publication_delay_h: 21.5` in the diagnostics below, i.e. the
newest value is one day old, not the 1–3 days the README warns about — the mock's fixture ends
"yesterday" on purpose.

### 5c. Statistics

The metadata the Energy-dashboard picker reads is complete and points at the entity ids:

```text
sensor.smart_0000_consumption_total | recorder | kWh | unit_class energy | has_sum True
sensor.smart_0001_consumption_total | recorder | kWh | unit_class energy | has_sum True
sensor.smart_0000_consumption_yesterday | recorder | kWh | unit_class energy | has_sum True
sensor.smart_0001_consumption_yesterday | recorder | kWh | unit_class energy | has_sum True
```

The rows themselves, read straight out of the recorder database in `/tmp/ha-fresh` (start timestamps are
local midnight, `+02:00` before and `+01:00` after the DST switch on 2026-10-25):

```text
sensor.smart_0001_consumption_total: 40 row(s), 2026-09-22T00:00:00+02:00 .. 2026-10-31T00:00:00+01:00, last sum 724.2
sensor.smart_0000_consumption_total: 39 row(s), 2026-09-22T00:00:00+02:00 .. 2026-10-31T00:00:00+01:00, last sum 668.7
```

`724.2` is the fixture's cumulative sum for `…0001` to the last day. `668.7` is `681.8 − 13.1`: the
fixture's one deliberately quality-flagged interval (2026-10-02, `anzahl_linien_p: 2`) is skipped
instead of summed, which is the documented behaviour (`statistics.py`, "a flagged interval is not
summed silently"), so `…0000` has 39 rows instead of 40. Every other row matches the fixture sum day
by day, which was checked with a scripted diff against `measurements_days.json`.

### 5d. The log

With `custom_components.ckw: info` in `configuration.yaml` (a stock install logs at WARNING and prints
none of this — see "Second attempt" below), one install produced (dates trimmed to the time of day, the
chunk lines 2/25 … 24/25 elided):

```text
21:28:23 INFO [custom_components.ckw.config_flow] Successfully authenticated
21:28:24 INFO [custom_components.ckw] statistics: starting the 730-day backfill for 0000000099 — Luzern at the first day
21:28:24 INFO [custom_components.ckw.statistics] backfill: importing 730 day(s) of statistics 2024-11-01..2026-10-31 for 0000000099 — Luzern (25 chunk(s) of 30 days)
21:28:24 INFO [custom_components.ckw.statistics] backfill: 2024-11-01..2024-11-30 done, 1/25
…                (chunks 2/25 … 24/25, one second apart)
21:28:50 INFO [custom_components.ckw.statistics] backfill: 2026-10-22..2026-10-31 done, 25/25
21:28:50 INFO [custom_components.ckw.statistics] backfill: finished 25/25 chunk(s) for 0000000099 — Luzern, 79 row(s) imported
21:28:50 INFO [custom_components.ckw] statistics: the backfill for 0000000099 — Luzern is done
```

The whole 730-day import took 26 s for two meters and wrote 79 rows (40 + 39, the difference again being
the flagged day). No `Traceback`, no `ERROR` from `custom_components.ckw` — the container log contains
two WARNING lines for the component, the loader notice and the deprecation below, and nothing else
besides Home Assistant's own `aiodhcpwatcher` error.

**The publication line did not appear, and cannot on a first install.** `CkwCoordinator._log_publication`
logs `new grid data published: …` only when the newest interval of a poll is later than the newest
interval of the previous poll; the first poll only sets the baseline (`previous is None` in
`coordinator.py`). With a two-hour poll interval and a fresh process, that needs a second poll in which
CKW actually published a new day. What the run does prove is that the publication was read correctly:
`Last published interval` is the fixture's newest interval, and the diagnostics carry
`publication_delay_h: 21.5`. A restart did not produce the line either (the baseline is in-memory), and
the restored entry needs no new login, which the restart verified.

### 5e. Diagnostics

`GET /api/diagnostics/config_entry/01M6CCS38X7CKJ3TR4KSZY2GKS` answered `200` with 3229 bytes. Redaction
probe against the raw body:

```text
'CH1003601234500000000000000000000'      present: False
'CH1003601234500000000000000000001'      present: False
'Musterweg'                             present: False
'Max Beispiel'                          present: False
'eyJ'                                   present: False
```

and the two blocks that carry the sensitive parts:

```json
"session": {"has_access_token": true, "has_refresh_token": true, "expires_at": "…", "expired": false},
"customer": {"id_hash": "156d3f"},
"meters": [{"deliverypoint": "*****************************0000", "model": "SMART",
            "type": "import", "fuel": "elec", "last_interval_end": "2026-10-31T23:00:00+00:00",
            "publication_delay_h": 21.5, "intervals_seen": 7, "quality_intervals": 0,
            "present": true, "stale_since": null}]
```

No token value, no full delivery point, no customer id, no address: booleans for the tokens, a hash for
the customer, the last four digits for the meter. `token` appears in the body only as the key names
`has_access_token` and `has_refresh_token`.

## Second attempt — what needed one

Three things were redone, and none of them implicates the integration.

1. **A hand-seeded `configuration.yaml` before the first start breaks Home Assistant.** The first
   attempt at the INFO-level run copied the stock `configuration.yaml` (which includes
   `automations.yaml`, `scripts.yaml`, `scenes.yaml`) into the empty config dir *before* the first
   start. Those three files do not exist yet, so Home Assistant booted into recovery mode:

   ```text
   ERROR [homeassistant.bootstrap] Failed to parse configuration.yaml: Unable to read file
   /config/automations.yaml. Activating recovery mode
   ERROR [homeassistant.config_entries] Cannot find integration ckw
   ```

   In recovery mode no custom integration is loaded, and the config flow answered
   `404 {"message":"Invalid handler specified"}`. Letting Home Assistant create its own config on the
   first start and appending the `logger:` block afterwards fixed it. A user who edits
   `configuration.yaml` by hand before the first start hits the same wall for any integration.

2. **The default log level hides every INFO line.** A stock instance logs at WARNING, so a stranger sees
   the loader WARNING and, after the entry is set up, the deprecation WARNING below — and nothing else.
   The verification added

   ```yaml
   logger:
     default: warning
     logs:
       custom_components.ckw: info
   ```

   and restarted to capture 5d. [How-to: debug a connection](../how-to/debug-connection.md) documents the
   same logger block for users.

3. **`manifest/list` is the wrong probe for "is the integration there".** It answered 130 domains and no
   `ckw`. The loader WARNING and the accepted config-flow handler in Step 1 are the checks that mean
   something; `GET /manifest.json` only proves the frontend serves its PWA manifest.

## Findings

- **Deprecation, no failure.** Every setup and every restart logs
  `Detected that custom integration 'ckw' calls device_registry.async_get_or_create with a deprecated
  via_device parameter; use via_device_id instead … This will stop working in Home Assistant 2027.8.0`.
  It comes from `CkwDeviceInfo` in `entity.py`, which passes the identifier tuple `via_device=(DOMAIN,
  entry_id)` as ADR-0003 prescribes. Nothing is broken today; a future Home Assistant will refuse it, and
  replacing the tuple with the service device's id is a decision to record, not a drive-by edit.
- **Two devices from one entry.** The mock returns import, export and gas meters; the entry follows the
  two electricity meters and filters the gas meter. The user asked for one delivery point and gets a
  second device for the feed-in meter. That is #134's dynamic-delivery-point design, and it is what
  [reference: entities](entities.md) describes, but a first-time user with a PV meter will notice it.
- **Statistics start at the first imported day, not at zero.** The first row already carries the first
  day's quantity (`23.6` kWh for `…0000`), because the cumulative sum is anchored on the oldest imported
  day. The Energy dashboard reads it correctly; a reader who expects a `0` at the left edge should know.
- **`publication_delay_h` was 21.5 h, `intervals_seen` 7.** Both are in the sensible range and neither
  raised the 96 h lag warning or the 4-day stall warning.
- **Nothing else.** No `Traceback`, no `ERROR` from `custom_components.ckw`, no reauth and
  `state: loaded` throughout, including after a restart. The integration raised no repair issue; the
  single issue in Developer Tools → Repairs is Home Assistant's own `country_not_configured`, because
  the API onboarding never sets a country or a timezone the way the UI setup does.

## What a new user sees

A stranger who adds `custom_components/ckw`, restarts, registers the client id and clicks through the
CKW login gets: one entry named `<customer id> — <city>`; one device per electricity meter named
`SMART ***…1234`; four enabled sensors whose values agree with what CKW published; the history import
running in the background — 26 s for 730 days against the mock, which only has 40 days of data — and a
clean diagnostics download. The only surprises waiting for them are the loader WARNING (normal for
every custom integration), the 1–3 day publication delay (documented, not a fault), and the disabled
HT/NT and quarter-hour sensors.

## Cleanup

```bash
podman rm -f ha-fresh
rm -rf /tmp/ha-fresh /tmp/ha-ckw-clone /tmp/ha-fresh-token
```

`ha-dev` and this repository's `config/` were never touched: no command above names the `ha-dev`
container, and every path is under `/tmp`.

## Related

- [How-to: install](../how-to/install.md) — the two install routes this page verified the manual one of.
- [Tutorial: getting started](../tutorial/getting-started.md) — the same path with every click and its expected result.
- [Reference: entities](entities.md) — the seven sensors, their ids and which one the Energy dashboard wants.
- [Reference: measured API behaviour](ckw-openenergy-api-verification.md) — the behaviour the mock replays, and where it is our reading rather than CKW's.
- [Explanation: architecture](../explanation/architecture.md) — the config flow, coordinator and statistics importer this run exercised.
- [Local development instance](../../scripts/dev/README.md) — `ha-dev`, the mock and the onboarding script the API steps here mirror.
- [PLAN.md](../../PLAN.md) — row #107, the task this page closes.
- [Documentation map](../README.md) — where this page sits among the quadrants.
