"""The CKW Smart Meter integration.

The config flow creates one entry per CKW customer, this module builds the
per-entry API client on the entry's OAuth2 session, and the coordinator and the
sensor platform land in their own tasks. Nothing here performs network I/O while
an entry is set up; the first refresh belongs to the coordinator (#71).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CkwClient
from .const import API_BASE_URL, DOMAIN, ENV_API_BASE_URL, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CkwRuntimeData:
    """Per-entry state shared with the platforms.

    Deliberately minimal: the coordinator (#71) is added by its own task, so no
    field here is invented ahead of it. The client already reads the token
    through Home Assistant's OAuth2 session, which refreshes it when needed.
    """

    entry_id: str
    api: CkwClient


type CkwConfigEntry = ConfigEntry[CkwRuntimeData]


def api_base_url() -> str:
    """Return the API base URL, honouring the local dev override."""
    return os.environ.get(ENV_API_BASE_URL, API_BASE_URL)


async def async_get_access_token(
    session: config_entry_oauth2_flow.OAuth2Session,
) -> str:
    """Return a valid access token, refreshing it through OAuth2 if needed."""
    await session.async_ensure_token_valid()
    return str(session.token["access_token"])


async def async_setup_entry(hass: HomeAssistant, entry: CkwConfigEntry) -> bool:
    """Set up one CKW account from a config entry."""
    _LOGGER.debug("Setting up %s entry %s", DOMAIN, entry.entry_id)
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    entry.runtime_data = CkwRuntimeData(
        entry_id=entry.entry_id,
        api=CkwClient(
            async_get_clientsession(hass),
            lambda: async_get_access_token(session),
            base_url=api_base_url(),
        ),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CkwConfigEntry) -> bool:
    """Unload one CKW account and everything it loaded."""
    _LOGGER.debug("Unloading %s entry %s", DOMAIN, entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and hasattr(entry, "runtime_data"):
        del entry.runtime_data
    return unloaded
