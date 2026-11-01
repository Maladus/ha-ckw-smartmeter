"""Tests for the CKW config, reauth, reconfigure and options flows.

The suite uses ``pytest-homeassistant-custom-component`` (PHCC), which installs
the Home Assistant version the image runs. PHCC pins that version through its own
release, so ``requirements_test.txt`` keeps it loosely bounded and the installed
release must stay in step with the HA image in ``scripts/dev/.env`` (currently
``HA_VERSION=stable``); a mismatch shows up as changed flow or config-entry APIs,
not as an integration failure.

No socket is opened: the OAuth2 implementation is a local fake and
``CkwClient.async_get_customer`` is patched, so the flow is exercised without the
mock IdP or the real API.
"""

from __future__ import annotations

import time
from typing import Any, Final

import pytest
from aiohttp import ClientError, ClientResponseError
from aiohttp.client_reqrep import RequestInfo
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.network import NoURLAvailableError
from multidict import CIMultiDict, CIMultiDictProxy
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.ckw.api import (
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwCustomer,
    CkwError,
    CkwLocation,
    CkwMeter,
)
from custom_components.ckw.const import (
    CONF_BACKFILL_ENABLED,
    CONF_CITY,
    CONF_CUSTOMER_ID,
    CONF_METER,
    CONF_POLL_HOURS,
    DOMAIN,
    Fuel,
    MeterModel,
    MeterType,
)

CUSTOMER_ID: Final = "0000000099"
CITY: Final = "Luzern"
DELIVERYPOINT_1: Final = "CH1003601234500000000000000000000"
DELIVERYPOINT_2: Final = "CH1003601234500000000000000000001"
DELIVERYPOINT_GAS: Final = "CH1003601234500000000000000000002"
DELIVERYPOINT_3: Final = "CH1003601234500000000000000000003"
TITLE: Final = f"{CUSTOMER_ID} — {CITY}"

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeOAuth2Implementation(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    """Local OAuth2 implementation that hands out one fixed token pair."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Store the Home Assistant instance."""
        self.hass = hass

    @property
    def name(self) -> str:
        """Return the implementation name."""
        return "CKW test IdP"

    @property
    def domain(self) -> str:
        """Return the auth implementation domain stored in the entry."""
        return DOMAIN

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Return a URL the test never opens."""
        return "https://id.example.invalid/authorize"

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Return the token pair the callback would resolve to."""
        return {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    async def _async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """Return a refreshed token pair."""
        return {
            **token,
            "access_token": "refreshed-access-token",
            "refresh_token": "refreshed-refresh-token",
            "expires_in": 3600,
        }


class SecondOAuth2Implementation(FakeOAuth2Implementation):
    """A second implementation, so the flow has to ask which one to use."""

    @property
    def name(self) -> str:
        """Return the implementation name."""
        return "CKW second test IdP"

    @property
    def domain(self) -> str:
        """Return a domain distinct from the first implementation's."""
        return f"{DOMAIN}_second"


def make_meter(
    deliverypoint: str,
    *,
    fuel: Fuel = Fuel.ELEC,
    meter_type: MeterType = MeterType.IMPORT,
) -> CkwMeter:
    """Build one meter of the measured customer shape."""
    return CkwMeter(
        id=deliverypoint[-4:],
        model=MeterModel.SMART,
        type=meter_type,
        fuel=fuel,
        deliverypointnumber=deliverypoint,
        supply_start=None,
        supply_end=None,
    )


def make_customer(*meters: CkwMeter, city: str = CITY) -> CkwCustomer:
    """Build a one-location customer around the given meters."""
    return CkwCustomer(
        id=CUSTOMER_ID,
        locations=(
            CkwLocation(
                id="0000000001",
                gpnr="0000000001",
                street="Musterweg 1",
                postal_code="6000",
                city=city,
                meters=meters,
            ),
        ),
    )


def register_implementation(hass: HomeAssistant) -> None:
    """Register the fake implementation as the only OAuth2 implementation."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, FakeOAuth2Implementation(hass)
    )


def patch_customer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    customer: CkwCustomer | None = None,
    error: Exception | None = None,
) -> None:
    """Patch the probe call; the flow still builds the real client object."""

    async def fake_customer(self: CkwClient) -> CkwCustomer:
        if error is not None:
            raise error
        assert customer is not None
        return customer

    monkeypatch.setattr(CkwClient, "async_get_customer", fake_customer)


async def complete_oauth(hass: HomeAssistant, result: FlowResult) -> FlowResult:
    """Drive an initialized flow through the fake OAuth2 external step."""
    if result["type"] == "form" and result["step_id"] == "reauth_confirm":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    if result["type"] == "form" and result["step_id"] == "pick_implementation":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"implementation": DOMAIN}
        )

    assert result["type"] == "external"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "code": "authorization-code",
            "state": {"redirect_uri": "https://ha.example.invalid/auth/external/callback"},
        },
    )
    assert result["type"] == "external_done"
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def start_user_flow(hass: HomeAssistant) -> FlowResult:
    """Start a user flow and complete the OAuth2 step."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await complete_oauth(hass, result)


def add_entry(hass: HomeAssistant, *, options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Add a configured CKW entry whose entry id must survive reauth."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CUSTOMER_ID: CUSTOMER_ID,
            CONF_CITY: CITY,
            CONF_METER: DELIVERYPOINT_1,
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "old-refresh-token",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "token_type": "Bearer",
            },
        },
        options=options or {},
        unique_id=CUSTOMER_ID,
        title=TITLE,
    )
    entry.add_to_hass(hass)
    return entry


async def test_single_meter_skips_the_picker(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-meter account creates the entry without asking for a delivery point."""
    register_implementation(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(
            make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_GAS, fuel=Fuel.GAS)
        ),
    )

    result = await start_user_flow(hass)

    assert result["type"] == "create_entry"
    assert result["title"] == TITLE
    assert result["data"][CONF_CUSTOMER_ID] == CUSTOMER_ID
    assert result["data"][CONF_CITY] == CITY
    assert result["data"][CONF_METER] == DELIVERYPOINT_1
    assert result["data"]["auth_implementation"] == DOMAIN

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].unique_id == CUSTOMER_ID
    assert entries[0].title == TITLE


async def test_two_meters_show_the_picker_and_store_the_choice(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two electricity meters produce a picker; the chosen delivery point is stored."""
    register_implementation(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_2)),
    )

    result = await start_user_flow(hass)

    assert result["type"] == "form"
    assert result["step_id"] == "meter"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER: DELIVERYPOINT_2}
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_METER] == DELIVERYPOINT_2
    assert result["title"] == TITLE


async def test_gas_only_account_aborts_no_meters(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A customer with no electricity meter cannot be configured."""
    register_implementation(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_GAS, fuel=Fuel.GAS)),
    )

    result = await start_user_flow(hass)

    assert result["type"] == "abort"
    assert result["reason"] == "no_meters"
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_same_customer_cannot_be_added_twice(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The customer id is the unique id, so the second setup aborts."""
    register_implementation(hass)
    patch_customer(monkeypatch, customer=make_customer(make_meter(DELIVERYPOINT_1)))
    first = await start_user_flow(hass)
    assert first["type"] == "create_entry"

    second = await start_user_flow(hass)

    assert second["type"] == "abort"
    assert second["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CkwAuthError("rejected"), "invalid_auth"),
        (CkwConnectionError("offline"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_probe_failures_are_mapped_to_abort_reasons(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    reason: str,
) -> None:
    """Auth, connection and unknown failures keep their own abort reasons."""
    register_implementation(hass)
    patch_customer(monkeypatch, error=error)

    result = await start_user_flow(hass)

    assert result["type"] == "abort"
    assert result["reason"] == reason
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_reauth_updates_the_entry_and_keeps_its_id(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reauth replaces the token but not the entry id or the selected meter."""
    register_implementation(hass)
    entry = add_entry(hass)
    entry_id = entry.entry_id
    patch_customer(monkeypatch, customer=make_customer(make_meter(DELIVERYPOINT_1)))
    # Reloading is not what this test checks, and the sensor platform lands later.
    monkeypatch.setattr(
        hass.config_entries,
        "async_schedule_reload",
        lambda entry_id: None,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
    )
    result = await complete_oauth(hass, result)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.entry_id == entry_id
    assert entry.unique_id == CUSTOMER_ID
    assert entry.data[CONF_METER] == DELIVERYPOINT_1
    assert entry.data["token"]["access_token"] == "test-access-token"
    assert entry.data["token"]["refresh_token"] == "test-refresh-token"


async def test_reconfigure_changes_the_delivery_point_and_keeps_the_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconfigure can switch meters without deleting or recreating the entry."""
    register_implementation(hass)
    entry = add_entry(hass)
    entry_id = entry.entry_id
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_2)),
    )
    monkeypatch.setattr(
        hass.config_entries,
        "async_schedule_reload",
        lambda entry_id: None,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "meter"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER: DELIVERYPOINT_2}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.entry_id == entry_id
    assert entry.data[CONF_METER] == DELIVERYPOINT_2
    assert entry.unique_id == CUSTOMER_ID


async def test_options_flow_stores_the_poll_interval(hass: HomeAssistant) -> None:
    """The options flow persists the selected poll interval."""
    entry = add_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_HOURS: 4, CONF_BACKFILL_ENABLED: True}
    )

    assert result["type"] == "create_entry"
    assert entry.options[CONF_POLL_HOURS] == 4
    assert entry.options[CONF_BACKFILL_ENABLED] is True


async def test_options_flow_can_disable_the_backfill(hass: HomeAssistant) -> None:
    """The options flow lets a user stop a backfill that keeps running."""
    entry = add_entry(hass, options={CONF_BACKFILL_ENABLED: True})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_HOURS: 4, CONF_BACKFILL_ENABLED: False}
    )

    assert result["type"] == "create_entry"
    assert entry.options[CONF_BACKFILL_ENABLED] is False


async def test_missing_oauth_configuration_aborts(
    hass: HomeAssistant,
) -> None:
    """Without a registered implementation the user step cannot start OAuth."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == "abort"
    assert result["reason"] == "missing_configuration"


async def test_application_credentials_dev_override(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dev environment variables replace the production OAuth endpoints."""
    from custom_components.ckw import application_credentials
    from custom_components.ckw.const import (
        ENV_AUTHORIZE_URL,
        ENV_TOKEN_URL,
        MOCK_AUTHORIZE_URL,
        MOCK_TOKEN_URL,
    )

    monkeypatch.setenv(ENV_AUTHORIZE_URL, MOCK_AUTHORIZE_URL)
    monkeypatch.setenv(ENV_TOKEN_URL, MOCK_TOKEN_URL)

    server = await application_credentials.async_get_authorization_server(hass)
    implementation = await application_credentials.async_get_auth_implementation(
        hass,
        DOMAIN,
        application_credentials.ClientCredential("test-client", "ignored-secret"),
    )

    assert server.authorize_url == MOCK_AUTHORIZE_URL
    assert server.token_url == MOCK_TOKEN_URL
    assert implementation.client_id == "test-client"
    assert implementation.client_secret == ""
    assert implementation.extra_authorize_data["scope"] == "openid offline_access"


# ---------------------------------------------------------------------------
# Error paths and edge cases (#63, #70, Silver test-coverage)
# ---------------------------------------------------------------------------


async def test_an_unavailable_oauth_implementation_aborts_the_user_step(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home Assistant can report the implementation itself as unavailable."""
    from homeassistant.helpers.config_entry_oauth2_flow import ImplementationUnavailableError

    from custom_components.ckw import config_flow as config_flow_module

    async def unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ImplementationUnavailableError("no implementation")

    monkeypatch.setattr(
        config_flow_module.config_entry_oauth2_flow,
        "async_get_implementations",
        unavailable,
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == "abort"
    assert result["reason"] == "missing_configuration"


async def test_a_customer_without_an_id_cannot_be_configured(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The customer id is the unique id; without it the flow stops at once."""
    from dataclasses import replace

    register_implementation(hass)
    patch_customer(
        monkeypatch,
        customer=replace(make_customer(make_meter(DELIVERYPOINT_1)), id=""),
    )

    result = await start_user_flow(hass)

    assert result["type"] == "abort"
    assert result["reason"] == "unknown"


async def test_an_electricity_meter_without_a_delivery_point_is_ignored(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A placeholder meter must not become the entry's delivery point."""
    register_implementation(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(
            make_meter(""),
            make_meter(DELIVERYPOINT_1),
        ),
    )

    result = await start_user_flow(hass)

    assert result["type"] == "create_entry"
    assert result["data"][CONF_METER] == DELIVERYPOINT_1


async def test_the_meter_step_rejects_a_delivery_point_it_never_offered(
    hass: HomeAssistant,
) -> None:
    """A stale form cannot store a delivery point that is no longer in the payload."""
    from custom_components.ckw.config_flow import CkwConfigFlow

    customer = make_customer(make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_2))
    flow = CkwConfigFlow()
    flow.hass = hass
    flow._customer = customer
    flow._meters = flow._electric_meters(customer)

    result = await flow.async_step_meter({CONF_METER: "CH-not-offered"})

    assert result["type"] == "form"
    assert result["step_id"] == "meter"
    assert result["errors"] == {CONF_METER: "invalid_meter"}


async def test_the_meter_step_without_a_customer_aborts_no_meters(hass: HomeAssistant) -> None:
    """The step guards itself while the OAuth2 callback has not produced a customer."""
    from custom_components.ckw.config_flow import CkwConfigFlow

    flow = CkwConfigFlow()
    flow.hass = hass

    result = await flow.async_step_meter()

    assert result["type"] == "abort"
    assert result["reason"] == "no_meters"


async def test_the_picker_and_title_can_do_without_a_city(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A location without a city yields the bare delivery point and customer id."""
    register_implementation(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_2), city=""),
    )
    result = await start_user_flow(hass)
    assert result["step_id"] == "meter"

    # The picker renders both labels, neither of which has a city suffix.
    options = result["data_schema"]({CONF_METER: DELIVERYPOINT_1})
    assert options[CONF_METER] == DELIVERYPOINT_1

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER: DELIVERYPOINT_2}
    )

    assert result["type"] == "create_entry"
    assert result["title"] == CUSTOMER_ID
    assert result["data"][CONF_CITY] == ""


async def test_a_reconfigure_without_a_known_customer_uses_one_meter_directly(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-meter account reconfigures without showing the picker."""
    register_implementation(hass)
    entry = add_entry(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_1), city="Bern"),
    )
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_METER] == DELIVERYPOINT_1
    assert entry.data[CONF_CITY] == "Bern"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CkwAuthError("rejected"), "invalid_auth"),
        (CkwConnectionError("offline"), "cannot_connect"),
        (CkwError("unexpected answer"), "unknown"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_reconfigure_failures_abort_with_the_probe_reason(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    reason: str,
) -> None:
    """A broken probe during reconfigure keeps the entry and names the reason."""
    register_implementation(hass)
    entry = add_entry(hass)
    patch_customer(monkeypatch, error=error)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )

    assert result["type"] == "abort"
    assert result["reason"] == reason
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_reconfigure_without_electricity_meters_aborts_no_meters(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gas-only answer cannot reconfigure an electricity entry."""
    register_implementation(hass)
    entry = add_entry(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_GAS, fuel=Fuel.GAS)),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "no_meters"


async def test_reconfigure_rejects_an_unknown_delivery_point(hass: HomeAssistant) -> None:
    """The reconfigure helper validates its choice like the setup picker."""
    from custom_components.ckw.config_flow import CkwConfigFlow

    customer = make_customer(make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_2))
    flow = CkwConfigFlow()
    flow.hass = hass
    flow._customer = customer
    flow._meters = flow._electric_meters(customer)

    result = flow._async_update_reconfigure("CH-not-offered")

    assert result["type"] == "form"
    assert result["step_id"] == "meter"
    assert result["errors"] == {CONF_METER: "invalid_meter"}


def test_reconfigure_without_a_customer_aborts_no_meters(hass: HomeAssistant) -> None:
    """The update helper refuses to touch an entry before the probe succeeded."""
    from custom_components.ckw.config_flow import CkwConfigFlow

    flow = CkwConfigFlow()
    flow.hass = hass

    result = flow._async_update_reconfigure(DELIVERYPOINT_1)

    assert result["type"] == "abort"
    assert result["reason"] == "no_meters"


async def test_reconfigure_aborts_when_the_oauth_implementation_is_gone(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted application credential leaves reconfigure nothing to authenticate with."""
    from homeassistant.helpers.config_entry_oauth2_flow import ImplementationUnavailableError

    from custom_components.ckw import config_flow as config_flow_module

    entry = add_entry(hass)

    async def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise ImplementationUnavailableError("gone")

    monkeypatch.setattr(
        config_flow_module.config_entry_oauth2_flow,
        "async_get_config_entry_implementation",
        unavailable,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "unknown"


async def test_reauth_moves_to_the_only_meter_when_the_old_one_is_gone(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-meter account keeps working after CKW renumbers its delivery point."""
    register_implementation(hass)
    entry = add_entry(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_2), city="Bern"),
    )
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
    )
    result = await complete_oauth(hass, result)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_METER] == DELIVERYPOINT_2
    assert entry.data[CONF_CITY] == "Bern"


async def test_the_options_flow_falls_back_for_a_stored_interval_outside_the_bounds(
    hass: HomeAssistant,
) -> None:
    """An old or hand-edited option value cannot break the form."""
    from custom_components.ckw.const import DEFAULT_POLL_HOURS

    entry = add_entry(hass, options={CONF_POLL_HOURS: 99})

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    schema_default = next(
        key.default() for key in result["data_schema"].schema if key == CONF_POLL_HOURS
    )
    assert schema_default == DEFAULT_POLL_HOURS


def test_the_debug_helpers_mask_a_location_without_a_city() -> None:
    """The label and title helpers stay usable when CKW sends no city."""
    from custom_components.ckw.config_flow import CkwConfigFlow

    meter = make_meter(DELIVERYPOINT_1)
    location = CkwLocation(
        id="1",
        gpnr="1",
        street="Musterweg 1",
        postal_code="6000",
        city=None,
        meters=(meter,),
    )

    assert CkwConfigFlow._meter_label(meter, location) == DELIVERYPOINT_1
    assert CkwConfigFlow._entry_title(CUSTOMER_ID, "") == CUSTOMER_ID


async def test_two_implementations_ask_which_one_to_use(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two OAuth2 implementations reach the inherited picker instead of the only one."""
    register_implementation(hass)
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, SecondOAuth2Implementation(hass)
    )
    patch_customer(monkeypatch, customer=make_customer(make_meter(DELIVERYPOINT_1)))

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == "form"
    assert result["step_id"] == "pick_implementation"

    result = await complete_oauth(hass, result)

    assert result["type"] == "create_entry"
    assert result["data"][CONF_METER] == DELIVERYPOINT_1


async def test_reconfigure_reuses_the_customer_it_already_probed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second visit of the reconfigure step does not probe the account again."""
    from custom_components.ckw.config_flow import CkwConfigFlow as Flow

    register_implementation(hass)
    entry = add_entry(hass)
    customer = make_customer(make_meter(DELIVERYPOINT_1), make_meter(DELIVERYPOINT_2))
    probe_calls: list[CkwCustomer] = []

    async def probe(self: CkwClient) -> CkwCustomer:
        probe_calls.append(customer)
        return customer

    monkeypatch.setattr(CkwClient, "async_get_customer", probe)
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    flow = Flow()
    flow.hass = hass
    flow.context = {"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}

    first = await flow.async_step_reconfigure()
    second = await flow.async_step_reconfigure()

    assert first["step_id"] == "meter"
    assert second["type"] == "form"
    assert second["step_id"] == "meter"
    assert second["description_placeholders"] == {"count": "2"}
    assert len(probe_calls) == 1


async def test_reauth_keeps_the_stored_delivery_point_when_two_meters_are_offered(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reauth does not silently move the entry to a meter the user never chose."""
    register_implementation(hass)
    entry = add_entry(hass)
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_2), make_meter(DELIVERYPOINT_3)),
    )
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
    )
    result = await complete_oauth(hass, result)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_METER] == DELIVERYPOINT_1
    assert entry.data[CONF_CITY] == CITY


async def test_reauth_without_a_stored_delivery_point_stores_none(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry that never had a delivery point is not given one behind the user's back."""
    register_implementation(hass)
    entry = add_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={key: value for key, value in entry.data.items() if key != CONF_METER}
    )
    patch_customer(
        monkeypatch,
        customer=make_customer(make_meter(DELIVERYPOINT_2), make_meter(DELIVERYPOINT_3)),
    )
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", lambda entry_id: None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
    )
    result = await complete_oauth(hass, result)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert CONF_METER not in entry.data
    assert entry.data[CONF_CUSTOMER_ID] == CUSTOMER_ID
    assert entry.data["token"]["access_token"] == "test-access-token"


@pytest.mark.parametrize("option", [CONF_POLL_HOURS, CONF_BACKFILL_ENABLED])
async def test_the_options_form_defaults_come_from_the_entry(
    hass: HomeAssistant, option: str
) -> None:
    """The form opens on the stored value, and on the fresh-install value without one."""
    from custom_components.ckw.const import DEFAULT_POLL_HOURS

    fresh = add_entry(hass)
    configured = add_entry(hass, options={CONF_POLL_HOURS: 6, CONF_BACKFILL_ENABLED: False})

    fresh_result = await hass.config_entries.options.async_init(fresh.entry_id)
    configured_result = await hass.config_entries.options.async_init(configured.entry_id)

    def default_of(result: FlowResult) -> Any:
        return next(key.default() for key in result["data_schema"].schema if key == option)

    if option == CONF_POLL_HOURS:
        assert default_of(fresh_result) == DEFAULT_POLL_HOURS
        assert default_of(configured_result) == 6
    else:
        assert default_of(fresh_result) is True
        assert default_of(configured_result) is False


# ---------------------------------------------------------------------------
# The abort paths of Home Assistant's OAuth2 helper, reached through this flow
# ---------------------------------------------------------------------------


def token_exchange_error(kind: str) -> Exception | dict[str, Any]:
    """Return what the token endpoint gives back for one failure mode."""
    if kind == "unauthorized":
        request_info = RequestInfo(
            URL("https://id.example.invalid/token"),
            "POST",
            CIMultiDictProxy(CIMultiDict()),
        )
        return ClientResponseError(request_info, (), status=401)
    if kind == "client_error":
        return ClientError("the token endpoint is unreachable")
    if kind == "timeout":
        return TimeoutError()
    return {"access_token": "test-access-token", "token_type": "Bearer"}


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("unauthorized", "oauth_unauthorized"),
        ("client_error", "oauth_failed"),
        ("timeout", "oauth_timeout"),
        ("no_expiry", "oauth_error"),
    ],
)
async def test_a_failed_token_exchange_aborts_with_its_reason(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    reason: str,
) -> None:
    """A rejected, unreachable, slow or malformed token answer never creates an entry."""
    register_implementation(hass)

    async def resolve(self: FakeOAuth2Implementation, external_data: Any) -> dict[str, Any]:
        answer = token_exchange_error(kind)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(FakeOAuth2Implementation, "async_resolve_external_data", resolve)

    result = await start_user_flow(hass)

    assert result["type"] == "abort"
    assert result["reason"] == reason
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "authorize_url_timeout"),
        (NoURLAvailableError(), "no_url_available"),
    ],
)
async def test_an_authorize_url_that_cannot_be_built_aborts(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    reason: str,
) -> None:
    """A slow IdP or an unreachable Home Assistant URL stops before the browser step."""
    register_implementation(hass)

    async def generate(self: FakeOAuth2Implementation, flow_id: str) -> str:
        raise error

    monkeypatch.setattr(FakeOAuth2Implementation, "async_generate_authorize_url", generate)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == "abort"
    assert result["reason"] == reason
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_a_sign_in_cancelled_at_the_idp_aborts(hass: HomeAssistant) -> None:
    """The user can decline at CKW; the flow says so instead of creating an entry."""
    register_implementation(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == "external"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"error": "access_denied"}
    )
    assert result["type"] == "external_done"

    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] == "abort"
    assert result["reason"] == "user_rejected_authorize"
    assert result["description_placeholders"] == {"error": "access_denied"}
    assert hass.config_entries.async_entries(DOMAIN) == []
