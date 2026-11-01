# Quality scale

This page answers: which rule of the Home Assistant quality scale does this integration meet, what
proves it, and which rules are still open (#101, #102, #137)?

The audit covers the 51 rules of Bronze (20), Silver (10) and Gold (21). Platinum is deliberately not
listed: it grades a separately published client library, not the integration, and is deferred until
Gold is verified ([PLAN.md](../../PLAN.md), [GOAL.md](../../GOAL.md)).

The machine-readable copy is
[custom_components/ckw/quality_scale.yaml](../../custom_components/ckw/quality_scale.yaml); this page is
the readable one and links every claim to the file, test or ADR behind it. `done` means the rule is met
today, `exempt` means the rule cannot apply and the reason is written down, `todo` means it is not met.

## Tier summary

| Tier | Rules | `done` | `exempt` | `todo` |
| --- | --- | --- | --- | --- |
| Bronze | 20 | 17 | 3 | 0 |
| Silver | 10 | 10 | 0 | 0 |
| Gold | 21 | 19 | 2 | 0 |
| **Total** | **51** | **46** | **5** | **0** |

A tier counts as reached only when every rule of that tier **and every lower tier** is `done` or
`exempt`. Bronze, Silver and Gold all are, so `manifest.json` claims `"quality_scale": "gold"`; the
five `exempt` rules state why they cannot apply.

## Bronze

| Rule | Status | Evidence |
| --- | --- | --- |
| `action-setup` | done | `ckw.backfill` is registered from `async_setup`, so it exists as soon as the domain loads ([`__init__.py:96`](../../custom_components/ckw/__init__.py) → [statistics.py:707](../../custom_components/ckw/statistics.py)). The handler captures no entry: it resolves its target from `entry_id` or from every loaded entry and raises a translated `HomeAssistantError` when there is none; a second registration is a no-op. `test_the_action_exists_as_soon_as_the_domain_loads` in [test_services.py](../../tests/test_services.py). |
| `appropriate-polling` | done | Daily cloud data; default 2 h, options offer 1/2/4/6/12 h ([const.py:60-64](../../custom_components/ckw/const.py), [coordinator.py:215](../../custom_components/ckw/coordinator.py)); [ADR-0004](../explanation/decisions/0004-poll-interval-and-data-lag.md). |
| `brands` | done | [brand/](../../custom_components/ckw/brand/) with `icon.png` and `logo.png` (#12); the brands-repository PR belongs to #116. |
| `common-modules` | done | `DataUpdateCoordinator` ([coordinator.py:171](../../custom_components/ckw/coordinator.py)), `config_entry_oauth2_flow` ([`__init__.py:112`](../../custom_components/ckw/__init__.py)), `runtime_data`, `Store` ([repairs.py:99](../../custom_components/ckw/repairs.py), [statistics.py](../../custom_components/ckw/statistics.py)), `issue_registry` ([repairs.py:50](../../custom_components/ckw/repairs.py)), `DeviceInfo` ([entity.py:78](../../custom_components/ckw/entity.py)). |
| `config-flow` | done | [config_flow.py](../../custom_components/ckw/config_flow.py): OAuth2 user flow (`:92`), meter picker (`:140`), reauth (`:163`), reconfigure (`:178`), options (`:377`); every string, the OAuth2 abort reasons included, is in [strings.json](../../custom_components/ckw/strings.json) `config`/`options`. |
| `config-flow-test-coverage` | done | 100 % of [config_flow.py](../../custom_components/ckw/config_flow.py), statements **and** branches (191 statements, 58 branches): [test_config_flow.py](../../tests/test_config_flow.py) covers the two-implementation picker, every probe failure, the OAuth2 aborts (`missing_configuration`, `authorize_url_timeout`, `no_url_available`, `user_rejected_authorize`, `oauth_unauthorized`, `oauth_failed`, `oauth_timeout`, `oauth_error`), a gas-only account, invalid and missing delivery points, both reauth fallbacks, the reconfigure revisit and the options-form defaults. No line is excluded to reach the number. |
| `dependency-transparency` | exempt | No external dependency: `"requirements": []` ([manifest.json](../../custom_components/ckw/manifest.json)), and [api.py](../../custom_components/ckw/api.py) uses only the aiohttp Home Assistant ships. The rule's own exception for zero dependencies applies. |
| `docs-actions` | done | One action exists, `ckw.backfill`; what it does and what every field means are documented in [configuration](configuration.md#action-ckwbackfill), [services.yaml](../../custom_components/ckw/services.yaml) and `services.backfill` in [strings.json](../../custom_components/ckw/strings.json). |
| `docs-conditions` | exempt | No conditions: no condition platform, no `async_register_condition`, no `device_condition.py`. |
| `docs-high-level-description` | done | [README.md](../../README.md): what it does, what it deliberately does not do; [GOAL.md](../../GOAL.md). |
| `docs-installation-instructions` | done | [tutorial: getting started](../tutorial/getting-started.md), [how-to: install](../how-to/install.md) (HACS and manual), README quickstart. |
| `docs-removal-instructions` | done | [how-to: install](../how-to/install.md) → "Remove" and "Delete the leftover statistics and entities". |
| `docs-triggers` | exempt | No triggers: no trigger platform, no `async_register_trigger`, no `device_trigger.py`. |
| `entity-event-setup` | done | Subscriptions are made in `async_setup_entry` and released with the entry: `entry.async_on_unload(coordinator.async_add_listener(...))` ([sensor.py:239](../../custom_components/ckw/sensor.py)). |
| `entity-unique-id` | done | `meter_entity_unique_id()` is the one definition of `{entry_id}_{deliverypoint}_{key}` ([entity.py:44](../../custom_components/ckw/entity.py)), set by every entity ([entity.py:116](../../custom_components/ckw/entity.py)); append-only per [conventions](conventions.md), [ADR-0003](../explanation/decisions/0003-meter-device-mapping.md). |
| `has-entity-name` | done | `_attr_has_entity_name = True` ([entity.py:98](../../custom_components/ckw/entity.py)), a `translation_key` per description ([sensor.py:117-178](../../custom_components/ckw/sensor.py)) and the names in `entity.sensor` of [strings.json](../../custom_components/ckw/strings.json). |
| `runtime-data` | done | `CkwRuntimeData` ([`__init__.py:60`](../../custom_components/ckw/__init__.py)) hung on the typed entry ([`__init__.py:75`](../../custom_components/ckw/__init__.py)); platforms read `entry.runtime_data`. |
| `test-before-configure` | done | The flow probes `GET /smartmeter/customer` before creating the entry ([config_flow.py:111-126](../../custom_components/ckw/config_flow.py)) and aborts with `invalid_auth`, `cannot_connect` or `unknown`; `test_probe_failures_are_mapped_to_abort_reasons` in [test_config_flow.py](../../tests/test_config_flow.py). |
| `test-before-setup` | done | `await coordinator.async_config_entry_first_refresh()` ([`__init__.py:131`](../../custom_components/ckw/__init__.py)) raises `ConfigEntryNotReady`/`ConfigEntryAuthFailed` before any platform is forwarded; `test_a_failed_first_refresh_is_not_ready` in [test_coordinator.py](../../tests/test_coordinator.py). |
| `unique-config-entry` | done | `async_set_unique_id(customer.id)` plus `_abort_if_unique_id_configured()` ([config_flow.py:128,134](../../custom_components/ckw/config_flow.py)); `test_same_customer_cannot_be_added_twice` in [test_config_flow.py](../../tests/test_config_flow.py). |

## Silver

| Rule | Status | Evidence |
| --- | --- | --- |
| `action-exceptions` | done | [exceptions.py](../../custom_components/ckw/exceptions.py) maps every failure to a translated `HomeAssistantError`; `ckw.backfill` raises them ([statistics.py:715-726](../../custom_components/ckw/statistics.py)), asserted by [test_exceptions.py](../../tests/test_exceptions.py). |
| `config-entry-unloading` | done | `async_unload_entry` unloads the platforms and drops the runtime data ([`__init__.py:216`](../../custom_components/ckw/__init__.py)); the coordinator registers its shutdown as an entry unload callback; `test_setup_registers_the_service_device_and_unload_clears_the_entry` in [test_coordinator.py](../../tests/test_coordinator.py). |
| `docs-configuration-parameters` | done | [configuration](configuration.md): both options fields with type, allowed values, default and effect, generated from `config_flow.py:377-403` and `strings.json` `options`. |
| `docs-installation-parameters` | done | [configuration](configuration.md): the application credential (client id, empty secret, redirect URI), the endpoints and the development overrides, generated from [application_credentials.py](../../custom_components/ckw/application_credentials.py) and `strings.json`. |
| `entity-unavailable` | done | `CkwEntity.available` requires a successful poll and the meter in `CkwData.meters_present` ([entity.py:120](../../custom_components/ckw/entity.py)); `test_a_failed_poll_makes_every_sensor_unavailable_and_recovery_restores_it` and `test_a_meter_without_data_is_unavailable_not_removed` in [test_sensor.py](../../tests/test_sensor.py). |
| `integration-owner` | done | `"codeowners": ["@Maladus"]` in [manifest.json](../../custom_components/ckw/manifest.json). |
| `log-when-unavailable` | done | `CkwCoordinator._log_failure` writes one warning on the first failure, debug while the cause repeats, and the coordinator logs the recovery at info ([coordinator.py:549](../../custom_components/ckw/coordinator.py)); `test_a_failure_warns_once_and_the_recovery_is_info` in [test_coordinator.py](../../tests/test_coordinator.py). |
| `parallel-updates` | done | `PARALLEL_UPDATES = 0` with the reason in the module docstring ([sensor.py:44-51](../../custom_components/ckw/sensor.py)); recorded in [conventions](conventions.md). |
| `reauthentication-flow` | done | `async_step_reauth`/`async_step_reauth_confirm` restart the OAuth2 flow and keep the entry id ([config_flow.py:163,167](../../custom_components/ckw/config_flow.py)), reached from `ConfigEntryAuthFailed` ([coordinator.py:398](../../custom_components/ckw/coordinator.py)); `test_reauth_updates_the_entry_and_keeps_its_id` in [test_config_flow.py](../../tests/test_config_flow.py), `test_an_auth_failure_asks_for_reauth` in [test_coordinator.py](../../tests/test_coordinator.py). |
| `test-coverage` | done | `fail_under = 95` and branch coverage in [pyproject.toml](../../pyproject.toml); the CI job runs `pytest --cov=custom_components/ckw --cov-report=term-missing` ([ci.yml](../../.github/workflows/ci.yml)); measured 99 % (1839 statements, 486 branches, 258 tests) — #136. |

## Gold

| Rule | Status | Evidence |
| --- | --- | --- |
| `devices` | done | One device per delivery point, grouped under the entry's service device: `CkwDeviceInfo` with `via_device=(DOMAIN, entry_id)` ([entity.py:78-93](../../custom_components/ckw/entity.py)) and the service device created in [`__init__.py:233`](../../custom_components/ckw/__init__.py); [ADR-0003](../explanation/decisions/0003-meter-device-mapping.md), #80. |
| `diagnostics` | done | [`diagnostics.py`](../../custom_components/ckw/diagnostics.py) builds the redacted config-entry dump: version, title and options, the customer id as a six-character `sha256`, one row per delivery point (masked through `api.mask_deliverypoint`, with model, type, fuel, newest interval, publication delay, interval and quality counts, presence and `stale_since`), the coordinator's health (success, the exception *type* only, the rate-limit headers), the statistics state and two token booleans with the expiry time. `_redact_secrets` then drops every remaining value under a token/secret/password/authorization key, keeping booleans; [test_diagnostics.py](../../tests/test_diagnostics.py) asserts the serialized dump holds no token-shaped string, no complete Zählpunkt, no e-mail address and the customer number only as its hash — #105. |
| `discovery` | exempt | Cloud service over HTTPS (`"iot_class": "cloud_polling"`); nothing on the LAN to discover, the account identity comes from the OAuth login. No `async_step_dhcp`/`zeroconf`/`ssdp`/`usb` exists. |
| `discovery-update-info` | exempt | No discovery source can supply device info: the meter metadata is read from `GET /smartmeter/customer` on every poll ([coordinator.py](../../custom_components/ckw/coordinator.py), `_async_fetch_customer`) and is fresher than any discovery payload. |
| `docs-data-update` | done | [data model](../explanation/data-model.md) → "Update cadence": poll interval, the self-healing 7-day window, the 1–3 day lag, the 730-day backfill; [ADR-0004](../explanation/decisions/0004-poll-interval-and-data-lag.md). |
| `docs-examples` | done | [examples](examples.md): five copy-paste examples (a consumption-threshold notification, a data-arrival notification with the lag, a dashboard card, a 72 h lag warning and an HT/NT load-shifting sketch), each naming the entity ids, units and opt-in sensors it assumes — #128. |
| `docs-known-limitations` | done | [data model](../explanation/data-model.md) → "Known limitations", one row per limitation a user will notice. |
| `docs-supported-devices` | done | [data model](../explanation/data-model.md) → "Supported devices" and [entities](entities.md) → "Not supported": CKW smart meters, electricity, several delivery points; gas meters, legacy meters and other grid operators are out. |
| `docs-supported-functions` | done | [entities](entities.md): every sensor with unit, device class, state class, `last_reset`, unique id, statistic id, cadence and Energy-dashboard eligibility. |
| `docs-troubleshooting` | done | [Debug a connection](../how-to/debug-connection.md): symptom → cause → fix for a stale value, a rejected login and `ConfigEntryAuthFailed`, `403`/client-id validation, `429`/quota and the backfill, an interrupted backfill, missing statistics, the log levels, what is redacted, the diagnostics path and the bug-report checklist — #99. A separate reauth how-to (#122) is still planned, but its symptom and fix are covered here and the rule is met. |
| `docs-use-cases` | done | [tutorial: getting started](../tutorial/getting-started.md) walks install → Energy dashboard; [README.md](../../README.md) → "What you get"; the HT/NT and CHF use cases are in [data model](../explanation/data-model.md). |
| `dynamic-devices` | done | The sensor platform adds a delivery point the poll starts to return, with its device, without a reload ([sensor.py:218,239](../../custom_components/ckw/sensor.py)); `test_a_new_delivery_point_gets_its_entities_without_a_reload` in [test_dynamic_devices.py](../../tests/test_dynamic_devices.py) — #134. |
| `entity-category` | done | `EntityCategory.DIAGNOSTIC` on `last_update` and `last_published_interval` ([sensor.py:139,147](../../custom_components/ckw/sensor.py)); the measurements stay primary data. |
| `entity-device-class` | done | `SensorDeviceClass.ENERGY` for the five kWh sensors and `TIMESTAMP` for the two diagnostic timestamps, each with the matching native unit ([sensor.py:118-178](../../custom_components/ckw/sensor.py)) — #80. |
| `entity-disabled-by-default` | done | High tariff, low tariff and quarter hour are registered with `entity_registry_enabled_default=False` ([sensor.py:158,168,178](../../custom_components/ckw/sensor.py)), so a fresh setup enables exactly four core entities per meter — #132. |
| `entity-translations` | done | A `translation_key` per description ([sensor.py:117-178](../../custom_components/ckw/sensor.py)) and the names in `entity.sensor` of [strings.json](../../custom_components/ckw/strings.json), translated in [translations/de.json](../../custom_components/ckw/translations/de.json) and `en.json`. |
| `exception-translations` | done | `strings.json` `exceptions` holds all 11 keys; each is raised as `HomeAssistantError` or `UpdateFailed` with `translation_domain`/`translation_key` ([coordinator.py:659-701](../../custom_components/ckw/coordinator.py), [exceptions.py](../../custom_components/ckw/exceptions.py)); [test_exceptions.py](../../tests/test_exceptions.py) asserts keys in both languages — #130. |
| `icon-translations` | done | [icons.json](../../custom_components/ckw/icons.json) maps every `translation_key` to an mdi icon; no `_attr_icon` remains in Python — #131. |
| `reconfiguration-flow` | done | `async_step_reconfigure` re-reads the account and moves the entry to another delivery point through `async_update_reload_and_abort`, keeping the entry id ([config_flow.py:178](../../custom_components/ckw/config_flow.py)); `test_reconfigure_changes_the_delivery_point_and_keeps_the_entry` in [test_config_flow.py](../../tests/test_config_flow.py). |
| `repair-issues` | done | [repairs.py](../../custom_components/ckw/repairs.py) raises and clears four issues per entry (`meter_exchanged`, `data_quality`, `stale_publication`, `quarter_hour_retention`), each with a `strings.json` `issues` entry — #133, #157. |
| `stale-devices` | done | A meter missing longer than `STALE_AFTER_DAYS` is reported in `CkwData.stale_since`, keeps its entities and history, goes unavailable and exposes `stale_since`; `async_remove_config_entry_device` lets the user delete the device without it being resurrected ([`__init__.py:185`](../../custom_components/ckw/__init__.py), [entity.py:62](../../custom_components/ckw/entity.py)); covered by [test_dynamic_devices.py](../../tests/test_dynamic_devices.py) — #134. |

## How to re-check

The commands are the gates of [PLAN.md](../../PLAN.md); none of them needs the CKW account.

```bash
mise run lint    # ruff check + ruff format --check
mise run test    # pytest with --cov, fails below 95 %
mise run hooks   # pre-commit run --all-files, gitleaks included
mise run docs    # markdownlint-cli2 + scripts/docs-check.sh, prints the quality-scale summary
mise run check   # lint + docs + test + hooks, the full local gate
```

- **The quality-scale summary** is printed by `mise run docs` (and therefore by `mise run check`):
  `scripts/docs-check.sh` counts the rules per tier against the group headers in
  [quality_scale.yaml](../../custom_components/ckw/quality_scale.yaml), rejects a status other than
  `done`/`todo`/`exempt`, rejects an empty `comment:`, and fails when a `todo` rule is missing from
  [Known gaps](#known-gaps). A rule removed from one file and not the other is a red gate, not a silent
  drift. The same script runs as a step of the CI `docs` job, so the audit is re-checked on every pull
  request as well.
- **hassfest** runs in CI on every push and pull request (job `Hassfest` in
  [ci.yml](../../.github/workflows/ci.yml)) and validates
  [manifest.json](../../custom_components/ckw/manifest.json),
  [strings.json](../../custom_components/ckw/strings.json), `icons.json` and the translations. hassfest
  checks a quality-scale file only for integrations inside the Home Assistant core repository, so here
  [quality_scale.yaml](../../custom_components/ckw/quality_scale.yaml) is our own audit checklist and
  `scripts/docs-check.sh` is what keeps it honest.
- **The coverage report**: `mise run test` prints the per-module table; add
  `--cov-report=html` to get `htmlcov/index.html`, or run
  `mise exec -- python -m pytest tests/ -q --cov=custom_components/ckw --cov-report=term-missing`
  for the table alone.

## Where this audit disagrees with PLAN.md

[PLAN.md](../../PLAN.md) expected `action-setup`, `docs-actions` and `dependency-transparency` to be
`exempt`. The rule texts only exempt the third: the integration does add an action, so both action
rules apply. `action-setup` is met by registering the action in `async_setup`, `docs-actions` by
documenting it; `dependency-transparency` holds on the rule's own exception.

| Rule | Plan said | Verified | Why |
| --- | --- | --- | --- |
| `action-setup` | exempt | done | The rule is about *where* an action is registered, not about whether one exists. `ckw.backfill` now lives in `async_setup`, so the rule applies and passes. |
| `docs-actions` | exempt | done | The integration does add an action, so the "no actions" exception does not apply; it is documented in [configuration](configuration.md#action-ckwbackfill). |
| `dependency-transparency` | exempt | exempt | The rule's own exception covers an integration with no external dependencies, which [manifest.json](../../custom_components/ckw/manifest.json) has (`"requirements": []`). |

PLAN.md has been corrected to match this page.

## Known gaps

Every open rule, with the issue that closes it.

None: every rule above is `done` or `exempt`. The section stays as the place an open rule is listed
again if one is reopened or a new Home Assistant rule is added.

## Related

- [custom_components/ckw/quality_scale.yaml](../../custom_components/ckw/quality_scale.yaml) — the same
  statuses, machine-readable, one `comment:` per rule.
- [Configuration](configuration.md) — the two Silver documentation rules this page leans on.
- [Entities](entities.md) — the Gold rules about device classes, categories and translations.
- [Data model](../explanation/data-model.md) — `docs-data-update`, `docs-known-limitations`,
  `docs-supported-devices`.
- [Conventions](conventions.md) — error mapping, logging and unique ids, rule by rule.
- [PLAN.md](../../PLAN.md) — the tasks behind every number here, and the gates that re-check them.
