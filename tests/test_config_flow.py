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
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw.api import (
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwCustomer,
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
