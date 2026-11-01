"""The CKW Smart Meter integration.

The config flow creates one entry per CKW customer (ADR-0008). This module builds
the per-entry API client on the entry's OAuth2 session and the coordinator that
owns it, then hands both to the platforms through ``entry.runtime_data`` (#67).
The first refresh happens here, before the platform setup, so a broken login
leaves the entry ``SETUP_RETRY`` with a ``ConfigEntryNotReady`` instead of a
half-loaded entry without data (#71).

Once the platforms exist, the statistics import starts: the initial 730-day
backfill runs once per entry in a background task, so setup is never blocked by
it, and every later poll re-imports the rolling window through the listener
``statistics.py`` registers (#86).
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
from homeassistant.helpers.typing import ConfigType

from .api import CkwClient
from .const import (
    API_BASE_URL,
    CONF_BACKFILL_ENABLED,
    DOMAIN,
    ENV_API_BASE_URL,
    MANUFACTURER,
    PLATFORMS,
    SERVICE_MODEL,
    VERSION,
)
from .coordinator import CkwCoordinator
from .entity import meter_device_identifier
from .statistics import (
    StatisticsState,
    async_backfill,
    async_import_polled_statistics,
    async_prepare_statistics,
    async_register_backfill_service,
    async_register_statistics,
)

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
    ``statistics_state`` is the backfill's progress (done/pending and the chunk
    offset), so a diagnostic or a service call can read it without the store.
    """

    api: CkwClient
    coordinator: CkwCoordinator
    statistics_state: StatisticsState


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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain's actions once, as soon as the integration loads.

    ``ckw.backfill`` belongs to the domain, not to one account: it is registered
    here so it exists before any entry does, and the handler resolves the entry
    it should repair from the call itself (``entry_id`` or every loaded entry).
    ``async_register_backfill_service`` is idempotent, so a second call after an
    unload and reload leaves the one handler in place.
    """
    async_register_backfill_service(hass)
    return True


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
    # Published before the first refresh on purpose: the repair check runs inside
    # that refresh and reads the coordinator back through the runtime data.
    entry.runtime_data = CkwRuntimeData(
        api=client,
        coordinator=coordinator,
        statistics_state=StatisticsState(),
    )
    # Before any platform is forwarded: a dead login or an unreachable API
    # raises ConfigEntryNotReady here, and nothing is left half-built.
    await coordinator.async_config_entry_first_refresh()
    _async_register_service_device(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The statistics need the entity ids, so this runs after the platform setup.
    # The backfill is a background task on purpose: two years of history must
    # not delay the entry's setup, and `hass.async_create_task` would, because
    # Home Assistant waits for pending tasks before it finishes starting.
    state = await async_prepare_statistics(hass, entry)
    async_register_statistics(hass, entry)
    backfill_enabled = bool(entry.options.get(CONF_BACKFILL_ENABLED, True))
    if state.done or not backfill_enabled:
        if not backfill_enabled and not state.done:
            _LOGGER.info(
                "statistics: the history backfill is disabled for %s, "
                "only the rolling window is imported",
                entry.title,
            )
        entry.async_create_background_task(
            hass,
            async_import_polled_statistics(hass, entry),
            name="ckw statistics import",
        )
    else:
        _LOGGER.info(
            "statistics: starting the %d-day backfill for %s at %s",
            state.days,
            entry.title,
            "the first day" if state.next_start is None else state.next_start.isoformat(),
        )
        entry.async_create_background_task(
            hass,
            _async_run_backfill(hass, entry),
            name="ckw statistics backfill",
        )
    return True


async def _async_run_backfill(hass: HomeAssistant, entry: CkwConfigEntry) -> None:
    """Run the entry's backfill and log how it ended (never blocks setup)."""
    state = entry.runtime_data.statistics_state
    try:
        # ``state.days`` is the job that was pending, so a restart of a resumed
        # job keeps its window instead of replacing it with the default one.
        await async_backfill(hass, entry, entry.runtime_data.coordinator, state.days)
    except Exception:  # noqa: BLE001 - a background task must not log a traceback
        _LOGGER.exception(
            "statistics: the backfill for %s failed, it resumes at the next setup",
            entry.title,
        )
    else:
        _LOGGER.info("statistics: the backfill for %s is done", entry.title)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting one vanished meter's device by hand (#134).

    Home Assistant asks this before it removes a device that belongs to a config
    entry, and hides the action when the integration does not implement it. A
    stale meter is not a repair issue, because CKW may list it again, so deleting
    the device is the user's call. The history that hangs off its statistics is
    theirs to keep or drop.

    The service device belongs to the entry itself and is never removable here.
    A per-meter device is removable only once its meter is gone from the current
    poll: a meter CKW still reports must keep its device, or the next poll would
    only register it again.
    """
    identifier = next((item for item in device_entry.identifiers if item[0] == DOMAIN), None)
    if identifier is None or identifier[1] == entry.entry_id:
        return False
    data = entry.runtime_data.coordinator.data
    if data is None:
        return False
    return any(
        meter_device_identifier(entry.entry_id, deliverypoint) == identifier
        and deliverypoint not in data.meters_present
        for deliverypoint in set(data.meter_info) | set(data.meters)
    )


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
