"""The CKW Smart Meter integration.

Skeleton only: the config flow, the API client, the coordinator and the sensor
platform land in their own tasks. Nothing here opens a network connection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .const import DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CkwRuntimeData:
    """Per-entry state shared with the platforms.

    Deliberately minimal: the API client (#58) and the coordinator (#71) are added
    by their own tasks, so no field here is invented ahead of them.
    """

    entry_id: str


type CkwConfigEntry = ConfigEntry[CkwRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: CkwConfigEntry) -> bool:
    """Set up one CKW account from a config entry."""
    _LOGGER.debug("Setting up %s entry %s", DOMAIN, entry.entry_id)
    entry.runtime_data = CkwRuntimeData(entry_id=entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CkwConfigEntry) -> bool:
    """Unload one CKW account and everything it loaded."""
    _LOGGER.debug("Unloading %s entry %s", DOMAIN, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
