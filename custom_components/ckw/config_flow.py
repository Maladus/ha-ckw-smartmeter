"""Config flow for the CKW Smart Meter integration.

One config entry represents one CKW customer (ADR-0008). Authentication is the
browser authorization-code flow with PKCE, implemented by Home Assistant's OAuth
2.0 helper: no password or client secret is stored. After the login the flow
probes ``GET /smartmeter/customer``, filters gas meters and asks for a delivery
point only when the account has more than one usable meter.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    ImplementationUnavailableError,
)
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from . import api_base_url, async_get_access_token
from .api import (
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwCustomer,
    CkwError,
    CkwLocation,
    CkwMeter,
    mask_deliverypoint,
)
from .const import (
    CONF_BACKFILL_ENABLED,
    CONF_CITY,
    CONF_CUSTOMER_ID,
    CONF_METER,
    CONF_POLL_HOURS,
    DEFAULT_POLL_HOURS,
    DOMAIN,
    POLL_INTERVAL_HOURS,
    Fuel,
)

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: What one usable meter plus the location it belongs to is stored as.
type MeterLocation = tuple[CkwMeter, CkwLocation]


class CkwConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 setup, reauth and reconfigure flows."""

    DOMAIN = DOMAIN
    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        super().__init__()
        self._oauth_data: dict[str, Any] = {}
        self._customer: CkwCustomer | None = None
        self._meters: dict[str, MeterLocation] = {}

    @property
    def logger(self) -> logging.Logger:
        """Return the module logger."""
        return _LOGGER

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CkwOptionsFlow:
        """Return the options flow for the poll interval."""
        return CkwOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start the OAuth2 external flow."""
        try:
            implementations = await config_entry_oauth2_flow.async_get_implementations(
                self.hass, DOMAIN
            )
        except ImplementationUnavailableError as err:
            self.logger.error("No OAuth2 implementations available: %s", err)
            return self.async_abort(reason="missing_configuration")

        if not implementations:
            return self.async_abort(reason="missing_configuration")

        if user_input is None and len(implementations) == 1:
            self.flow_impl = next(iter(implementations.values()))
            return await self.async_step_auth()

        return await self.async_step_pick_implementation(user_input)

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Validate the new token and continue with meter selection or reauth."""
        self._oauth_data = data
        client = self._client_for_oauth_data(data)
        customer, reason = await self._async_load_customer(client)
        if customer is None:
            return self.async_abort(reason=reason or "unknown")

        if not customer.id:
            self.logger.debug("the CKW customer payload has no customer id")
            return self.async_abort(reason="unknown")

        self._customer = customer
        self._meters = self._electric_meters(customer)
        if not self._meters:
            return self.async_abort(reason="no_meters")

        await self.async_set_unique_id(customer.id)

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="reauth_account_mismatch")
            return self._async_finish_reauth(customer)

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch(reason="reconfigure_account_mismatch")
            return await self.async_step_meter()

        self._abort_if_unique_id_configured()
        if len(self._meters) == 1:
            deliverypoint = next(iter(self._meters))
            return self._async_create_entry(customer, deliverypoint)
        return await self.async_step_meter()

    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask which delivery point this entry should read."""
        if self._customer is None or not self._meters:
            return self.async_abort(reason="no_meters")

        if user_input is not None:
            deliverypoint = str(user_input[CONF_METER])
            if deliverypoint not in self._meters:
                return self.async_show_form(
                    step_id="meter",
                    data_schema=self._meter_schema(),
                    errors={CONF_METER: "invalid_meter"},
                )
            if self.source == SOURCE_RECONFIGURE:
                return self._async_update_reconfigure(deliverypoint)
            return self._async_create_entry(self._customer, deliverypoint)

        return self.async_show_form(
            step_id="meter",
            data_schema=self._meter_schema(),
            description_placeholders={"count": str(len(self._meters))},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Ask the user to confirm reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Restart the OAuth2 flow for an existing entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"name": self._get_reauth_entry().title},
            )
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the delivery point without deleting the entry."""
        entry = self._get_reconfigure_entry()

        if self._customer is None:
            try:
                implementation = (
                    await config_entry_oauth2_flow.async_get_config_entry_implementation(
                        self.hass, entry
                    )
                )
            except ImplementationUnavailableError as err:
                self.logger.error("OAuth2 implementation unavailable: %s", err)
                return self.async_abort(reason="unknown")

            session = config_entry_oauth2_flow.OAuth2Session(self.hass, entry, implementation)
            client = CkwClient(
                async_get_clientsession(self.hass),
                lambda: async_get_access_token(session),
                base_url=api_base_url(),
            )
            customer, reason = await self._async_load_customer(client)
            if customer is None:
                return self.async_abort(reason=reason or "unknown")

            self._customer = customer
            self._meters = self._electric_meters(customer)
            if not self._meters:
                return self.async_abort(reason="no_meters")

        if user_input is not None:
            return self._async_update_reconfigure(str(user_input[CONF_METER]))

        if len(self._meters) == 1:
            return self._async_update_reconfigure(
                next(iter(self._meters)), reload_even_if_entry_is_unchanged=False
            )

        return self.async_show_form(
            step_id="meter",
            data_schema=self._meter_schema(),
            description_placeholders={"count": str(len(self._meters))},
        )

    def _async_update_reconfigure(
        self, deliverypoint: str, *, reload_even_if_entry_is_unchanged: bool = True
    ) -> ConfigFlowResult:
        """Update the entry's delivery point without changing its id."""
        if self._customer is None:
            return self.async_abort(reason="no_meters")
        meter_location = self._meters.get(deliverypoint)
        if meter_location is None:
            return self.async_show_form(
                step_id="meter",
                data_schema=self._meter_schema(),
                errors={CONF_METER: "invalid_meter"},
            )
        _, location = meter_location
        city = location.city or ""
        self.logger.debug("reconfiguring delivery point %s", mask_deliverypoint(deliverypoint))
        return self.async_update_reload_and_abort(
            self._get_reconfigure_entry(),
            data_updates={CONF_METER: deliverypoint, CONF_CITY: city},
            title=self._entry_title(self._customer.id, city),
            reason="reconfigure_successful",
            reload_even_if_entry_is_unchanged=reload_even_if_entry_is_unchanged,
        )

    def _async_create_entry(self, customer: CkwCustomer, deliverypoint: str) -> ConfigFlowResult:
        """Create the entry with the selected delivery point."""
        _, location = self._meters[deliverypoint]
        city = location.city or ""
        self.logger.debug("selected delivery point %s", mask_deliverypoint(deliverypoint))
        return self.async_create_entry(
            title=self._entry_title(customer.id, city),
            data={
                **self._oauth_data,
                CONF_CUSTOMER_ID: customer.id,
                CONF_CITY: city,
                CONF_METER: deliverypoint,
            },
        )

    def _async_finish_reauth(self, customer: CkwCustomer) -> ConfigFlowResult:
        """Update the existing entry with the new token, keeping its entry id."""
        entry = self._get_reauth_entry()
        existing = entry.data.get(CONF_METER)
        deliverypoint = str(existing) if existing else None
        city = str(entry.data.get(CONF_CITY, ""))

        if deliverypoint in self._meters:
            _, location = self._meters[deliverypoint]
            city = location.city or ""
        elif len(self._meters) == 1:
            deliverypoint = next(iter(self._meters))
            _, location = self._meters[deliverypoint]
            city = location.city or ""

        data_updates: dict[str, Any] = {
            **self._oauth_data,
            CONF_CUSTOMER_ID: customer.id,
            CONF_CITY: city,
        }
        if deliverypoint is not None:
            data_updates[CONF_METER] = deliverypoint
            self.logger.debug("reauth kept delivery point %s", mask_deliverypoint(deliverypoint))

        return self.async_update_reload_and_abort(
            entry,
            data_updates=data_updates,
            title=self._entry_title(customer.id, city),
        )

    def _meter_schema(self) -> vol.Schema:
        """Build the delivery-point picker."""
        options = [
            SelectOptionDict(value=deliverypoint, label=self._meter_label(meter, location))
            for deliverypoint, (meter, location) in self._meters.items()
        ]
        return vol.Schema(
            {
                vol.Required(CONF_METER, default=next(iter(self._meters))): SelectSelector(
                    SelectSelectorConfig(options=options)
                )
            }
        )

    @staticmethod
    def _meter_label(meter: CkwMeter, location: CkwLocation) -> str:
        """Describe a delivery point without changing the stored value."""
        if location.city:
            return f"{meter.deliverypointnumber} — {location.city}"
        return meter.deliverypointnumber

    @staticmethod
    def _entry_title(customer_id: str, city: str) -> str:
        """Build the `<customer id> — <city>` title decided in ADR-0008."""
        if city:
            return f"{customer_id} — {city}"
        return customer_id

    @staticmethod
    def _electric_meters(customer: CkwCustomer) -> dict[str, MeterLocation]:
        """Return the usable electricity meters, keyed by delivery point."""
        meters: dict[str, MeterLocation] = {}
        for location in customer.locations:
            for meter in location.meters:
                if meter.fuel != Fuel.ELEC:
                    _LOGGER.debug(
                        "ignoring non-electric meter %s",
                        mask_deliverypoint(meter.deliverypointnumber),
                    )
                    continue
                if not meter.deliverypointnumber:
                    _LOGGER.debug("ignoring an electricity meter without a delivery point")
                    continue
                meters.setdefault(meter.deliverypointnumber, (meter, location))
        return meters

    async def _async_load_customer(
        self, client: CkwClient
    ) -> tuple[CkwCustomer | None, str | None]:
        """Fetch the customer and translate failures into abort reasons."""
        try:
            return await client.async_get_customer(), None
        except CkwAuthError as err:
            self.logger.debug("CKW rejected the sign-in: %s", err)
            return None, "invalid_auth"
        except CkwConnectionError as err:
            self.logger.debug("CKW API is unreachable: %s", err)
            return None, "cannot_connect"
        except CkwError as err:
            self.logger.debug("CKW API returned an unexpected error: %s", err)
            return None, "unknown"
        except Exception as err:  # noqa: BLE001 - a flow must never crash on a probe
            self.logger.debug("unexpected CKW probe failure: %s", err)
            return None, "unknown"

    def _client_for_oauth_data(self, data: Mapping[str, Any]) -> CkwClient:
        """Build the probe client on the freshly issued OAuth2 token."""
        token_entry = ConfigEntry(
            data=data,
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=self.MINOR_VERSION,
            options=None,
            source=config_entries.SOURCE_USER,
            subentries_data=None,
            title="",
            unique_id=None,
            version=self.VERSION,
        )
        session = config_entry_oauth2_flow.OAuth2Session(self.hass, token_entry, self.flow_impl)
        return CkwClient(
            async_get_clientsession(self.hass),
            lambda: async_get_access_token(session),
            base_url=api_base_url(),
        )


class CkwOptionsFlow(config_entries.OptionsFlow):
    """Options for one CKW entry."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Store the poll interval and whether setup imports the history."""
        current_backfill = bool(self.config_entry.options.get(CONF_BACKFILL_ENABLED, True))
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_POLL_HOURS: user_input[CONF_POLL_HOURS],
                    CONF_BACKFILL_ENABLED: user_input[CONF_BACKFILL_ENABLED],
                }
            )

        current = self.config_entry.options.get(CONF_POLL_HOURS, DEFAULT_POLL_HOURS)
        if current not in POLL_INTERVAL_HOURS:
            current = DEFAULT_POLL_HOURS

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL_HOURS, default=current): vol.In(POLL_INTERVAL_HOURS),
                    vol.Required(CONF_BACKFILL_ENABLED, default=current_backfill): bool,
                }
            ),
        )
