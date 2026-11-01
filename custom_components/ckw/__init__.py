"""The CKW Smart Meter integration.

The config flow creates one entry per CKW customer (ADR-0008). This module builds
the per-entry API client on the entry's OAuth2 session and the coordinator that
owns it, then hands both to the platforms through ``entry.runtime_data`` (#67).
The first refresh happens here, before the platform setup, so a broken login
leaves the entry ``SETUP_RETRY`` with a ``ConfigEntryNotReady`` instead of a
half-loaded entry without data (#71).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType

from .api import CkwClient
from .const import (
    API_BASE_URL,
    DOMAIN,
    ENV_API_BASE_URL,
    MANUFACTURER,
    PLATFORMS,
    SERVICE_MODEL,
    VERSION,
)
from .coordinator import CkwCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CkwRuntimeData:
    """Per-entry state shared with the platforms.

    The client and the coordinator are built once per entry and live as long as
    it does; the coordinator's ``data`` carries the per-meter totals. A platform
    reads them, it never opens a session or calls the API itself.
    """

    api: CkwClient
    coordinator: CkwCoordinator


type CkwConfigEntry = ConfigEntry[CkwRuntimeData]


def api_base_url() -> str:
    """Return the API base URL, honouring the local dev override."""
    return os.environ.get(ENV_API_BASE_URL, API_BASE_URL)


async def async_get_access_token(
    session: config_entry_oauth2_flow.OAuth2Session,
) -> str:
    """Return a valid access token, refreshing it through OAuth2 if needed.

    ``async_ensure_token_valid`` runs under the session's own lock and stores a
    refreshed pair in the config entry before it is returned, which is the
    single-flight refresh ADR-0007 asks for.
    """
    await session.async_ensure_token_valid()
    return str(session.token["access_token"])


async def async_setup_entry(hass: HomeAssistant, entry: CkwConfigEntry) -> bool:
    """Set up one CKW account from a config entry."""
    _LOGGER.debug("Setting up %s entry %s", DOMAIN, entry.entry_id)
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    client = CkwClient(
        async_get_clientsession(hass),
        lambda: async_get_access_token(session),
        base_url=api_base_url(),
    )
    coordinator = CkwCoordinator(hass, entry, client)
    # Before any platform is forwarded: a dead login or an unreachable API
    # raises ConfigEntryNotReady here, and nothing is left half-built.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = CkwRuntimeData(api=client, coordinator=coordinator)
    _async_register_service_device(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CkwConfigEntry) -> bool:
    """Unload one CKW account and everything it loaded.

    The coordinator registered its shutdown as an entry unload callback when it
    was built, so the refresh timer is cancelled with the entry; this function
    only unloads the platforms and drops the state it holds.
    """
    _LOGGER.debug("Unloading %s entry %s", DOMAIN, entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    if hasattr(entry, "runtime_data"):
        del entry.runtime_data
    return True


@callback
def _async_register_service_device(hass: HomeAssistant, entry: CkwConfigEntry) -> None:
    """Create the service device every per-meter device hangs under (ADR-0003)."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=SERVICE_MODEL,
        name=entry.title,
        entry_type=DeviceEntryType.SERVICE,
        sw_version=VERSION,
    )
