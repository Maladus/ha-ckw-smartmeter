# Configuration

This page answers: which parameters does the CKW Smart Meter integration take at setup and in the
options flow, what does each one mean, and what does the integration store?

Lookup only: every field below is generated from `custom_components/ckw/config_flow.py`,
`custom_components/ckw/strings.json` and `custom_components/ckw/services.yaml`, so a field that is not
listed here does not exist. Steps for the setup itself live in
[tutorial: getting started](../tutorial/getting-started.md).

There is no YAML configuration. The integration is set up from the UI only (`config_flow: true` in
`manifest.json`), and everything it needs is stored in the config entry; nothing is read from
`configuration.yaml`.

## Installation parameters (application credentials)

CKW issues a **client id** per customer. It is not an integration field: Home Assistant asks for it
once, under Settings → Devices & services → **Application credentials** → **CKW Smart Meter**, before
the integration can start a login (`application_credentials.py`).

| Field | Required | Value | Stored as | Code |
| --- | --- | --- | --- | --- |
| Client ID | yes | The client id CKW issued for your Home Assistant redirect URI | `application_credentials` entry, `client_id` | `application_credentials.py:79-103` |
| Client secret | no | Leave it **empty**. CKW registers this integration as a public PKCE client, and the secret is ignored if you fill it in | never stored | `application_credentials.py:97` (`client_secret=""`) |

Three more parameters exist but are not fields, because they never change per user:

| Parameter | Default | Where it comes from |
| --- | --- | --- |
| Redirect URI | `https://<your Home Assistant URL>/auth/external/callback` | Home Assistant's OAuth2 helper; it must be registered with CKW ([how-to: request the CKW client](../how-to/request-ckw-client.md), planned, #120) |
| Authorize and token URL | `https://id.ckw.ch/realms/CKW-prod/protocol/openid-connect/{auth,token}` | `AUTHORIZE_URL`/`TOKEN_URL` in `const.py:37-38`, readable through `application_credentials.py:44-54` |
| Scopes | `openid offline_access` | `SCOPES` in `const.py:40`; `offline_access` is what keeps the login alive between polls (ADR-0007) |

## Setup fields (config entry)

The setup asks exactly one question, and only when the account has more than one electricity meter.
Everything else is filled in from the CKW answer.

| Key | Asked in step | Selector | Required | Meaning | Code |
| --- | --- | --- | --- | --- | --- |
| `meter` | `meter` | `SelectSelector`, one option per electricity meter, labelled `<deliverypointnumber> — <city>` | yes, defaulting to the first meter | The delivery point (Zählpunkt) this entry reads. With exactly one electricity meter the step is skipped | `config_flow.py:140-161,290-309` |

The form's title, description and field label come from `strings.json` `config.step.meter`
(`title`, `description`, `data.meter`); `strings.json` defines no `data_description` for this step, so
the label is the whole help text. A gas meter is never offered: meters with `fuel: gas` are filtered out
of the picker (`config_flow.py:319-337`).

The flow stores these values in the entry's `data`; none of them is a form field:

| Key | Written by | Meaning | Code |
| --- | --- | --- | --- |
| `customer_id` | the flow, from `GET /smartmeter/customer` | The CKW customer id; also the entry's unique id, so one CKW account cannot be added twice | `config_flow.py:128-134,245-258` |
| `city` | the flow, from the meter's location | Part of the entry title `<customer id> — <city>` | `config_flow.py:245-258,312-316` |
| `auth_implementation`, `token` | Home Assistant's OAuth2 helper | The implementation id and the access/refresh token pair. Tokens are refreshed in place and never logged (`docs/reference/conventions.md`, "Redaction") | `__init__.py:95-108` |

## Options fields

Options are edited later from the entry's **Configure** button. They are stored in the entry's
`options`, not in `data`, and both are written together by one form (`CkwOptionsFlow`,
`config_flow.py:377-403`).

| Key | Label (en / de) | Type | Allowed values | Default | Effect | Code |
| --- | --- | --- | --- | --- | --- | --- |
| `poll_hours` | "Poll interval (hours)" / "Abfrageintervall (Stunden)" | integer | `1`, `2`, `4`, `6`, `12` (`POLL_INTERVAL_HOURS`) | `2` (`DEFAULT_POLL_HOURS`) | How often the coordinator asks CKW for the rolling 7-day window; the interval is also the session keep-alive (ADR-0004, ADR-0007) | `config_flow.py:380-403`, `coordinator.py:215`, `const.py:60-64` |
| `backfill_enabled` | "Import the 730-day history on setup" / "730 Tage Historie beim Einrichten importieren" | boolean | `true`, `false` | `true` (also when the key is absent, so an older entry keeps backfilling) | Whether setup starts the 730-day statistics import in the background. Off means only the rolling window is imported | `config_flow.py:380-403`, `__init__.py:141-169`, `const.py:190-193` |

A value outside the list is not accepted by the form; a stored `poll_hours` outside 1–12 h (a hand-edited
`.storage` file) is ignored and the coordinator falls back to 2 h (`coordinator.py:636-653`).

The options are read when the entry is set up. A change therefore takes effect on the next reload or
restart of the entry: the integration does not register an options update listener.

## Action: `ckw.backfill`

The integration registers one action, `ckw.backfill` (`statistics.py:707-735`). It re-imports daily
aggregates as long-term statistics, which is how a meter swap or a recorder wipe is repaired. The sums
are recomputed from a stored anchor, so re-importing a window overwrites instead of adding up
(ADR-0002).

| Field | UI name | Type | Allowed values | Default | Meaning | Code |
| --- | --- | --- | --- | --- | --- | --- |
| `entry_id` | Config entry | `text` selector | an existing CKW config entry id | empty = every loaded entry | Which account to re-import. An unknown id fails with the translated `unknown_entry` error | `statistics.py:715-726,739-745` |
| `days` | Days | `number` selector, `min: 1`, `max: 3650`, unit `days` | 1–3650 (`MAX_BACKFILL_DAYS` = 5 × 730) | `730` (`BACKFILL_DAYS`) | How many days of history to import, ending yesterday | `statistics.py:739-745`, `const.py:65` |
| `force` | Force | `boolean` selector | `true`, `false` | `false` | Recompute the cumulative sum from the first imported day instead of continuing the stored sum, and ignore the stored checksum | `statistics.py:717,743` |

The same three fields are declared in `services.yaml` with names, descriptions, examples and selectors;
`strings.json` `services.backfill` repeats the user-facing text. The steps for using the action after a
meter exchange belong in [how-to: backfill history](../how-to/backfill-history.md) (planned, #123).

## Development-only endpoint overrides

Three environment variables point the integration at the local mock instead of CKW. Production
installations never set them; the defaults in `const.py` are used when they are unset. `scripts/dev/up.sh`
passes them into the dev container from `scripts/dev/.env.example`.

| Variable | Overrides | Read in | Mock value (`scripts/dev/.env.example`) |
| --- | --- | --- | --- |
| `CKW_API_BASE_URL` | `API_BASE_URL` (`const.py:34`) | `api_base_url()` in `__init__.py:77-79` | `http://host.containers.internal:8787/api/v1` |
| `CKW_AUTHORIZE_URL` | `AUTHORIZE_URL` (`const.py:37`) | `_authorization_server_urls()` in `application_credentials.py:44-54` | `.../realms/CKW-prod/protocol/openid-connect/auth` |
| `CKW_TOKEN_URL` | `TOKEN_URL` (`const.py:38`) | same function | `.../realms/CKW-prod/protocol/openid-connect/token` |

`const.py:181-183` also names the config keys `base_url`, `authorize_url` and `token_url`. They are
reserved names for a future per-entry endpoint override; today the config flow does not ask for them
and nothing reads them, so the environment variables above are the only override.

## Related

- [Entities](entities.md) — what the entry creates once it is configured: sensors, unique ids, statistics.
- [Quality scale](quality-scale.md) — `docs-installation-parameters` and `docs-configuration-parameters`
  rest on this page.
- [Tutorial: getting started](../tutorial/getting-started.md) — the same fields, in the order the setup asks for them.
- [Explanation: data model](../explanation/data-model.md) — why the poll interval and the 730-day backfill are what they are.
- [ADR-0004](../explanation/decisions/0004-poll-interval-and-data-lag.md) and
  [ADR-0007](../explanation/decisions/0007-token-lifetime-and-keepalive.md) — the interval as a decision, not a default.
