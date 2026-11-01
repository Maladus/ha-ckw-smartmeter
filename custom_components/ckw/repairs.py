"""Repair issues for the problems a CKW user can see and act on (#133, #157).

Home Assistant's repair dashboard is where a user looks when something is wrong
but Home Assistant keeps running. This module raises exactly four issues, one
per condition, and clears each one again when its condition is gone:

* ``meter_exchanged`` - CKW reports a new ``supply_start_date`` for a delivery
  point whose statistic id already carries the old meter's history. The series
  cannot continue across two physical meters (ADR-0002, ADR-0003), so the user
  has to know why the total and the Energy dashboard have a seam.
* ``data_quality`` - more than ``DATA_QUALITY_FLAG_INTERVALS`` intervals of the
  rolling window are flagged by ``anzahl_linien_* > 1``. Such an interval is
  left out of the totals and out of the statistics, so the total has a hole
  instead of a wrong number (``docs/explanation/data-model.md``).
* ``stale_publication`` - the newest published interval is older than
  ``PUBLICATION_STALL_DAYS``, i.e. CKW stopped publishing.
* ``quarter_hour_retention`` - the quarter-hour sensor is enabled while
  ``recorder.purge_keep_days`` is below ``QUARTER_HOUR_MIN_RETENTION_DAYS``, so
  its history is deleted earlier than the how-to recommends (ADR-0009, #157).

Three rules shape the code below:

* **One issue per condition per entry.** The issue id is
  ``f"{DOMAIN}_{entry.entry_id}_{condition}"``, so two CKW accounts (two config
  entries) never share or overwrite an issue, and a second poll never adds a
  second issue for the same condition.
* **Symmetric.** Every check both raises and deletes its issue, driven only by
  the condition, so an issue disappears on the poll after the condition cleared.
* **Never in the way of the data.** The checks run inside the coordinator's
  update, but a wrong or unreadable input skips a check instead of failing the
  poll: a repair issue is information, never a reason to lose a reading.

Nothing here performs I/O of its own beyond reading the per-entry repair store,
which remembers the ``supply_start_date`` each delivery point was first seen
with. That memory is what makes a meter exchange visible after a restart.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.recorder import DATA_INSTANCE
from homeassistant.helpers.storage import Store

from .api import CkwMeasurement, mask_deliverypoint
from .const import (
    DATA_QUALITY_FLAG_INTERVALS,
    DOCS_QUARTER_HOUR_RETENTION_URL,
    DOMAIN,
    ISSUE_DATA_QUALITY,
    ISSUE_METER_EXCHANGED,
    ISSUE_QUARTER_HOUR_RETENTION,
    ISSUE_STALE_PUBLICATION,
    PUBLICATION_STALL_DAYS,
    QUARTER_HOUR_ENTITY_KEY,
    QUARTER_HOUR_MIN_RETENTION_DAYS,
    ROLLING_WINDOW_DAYS,
    STORAGE_KEY_REPAIRS,
    STORAGE_VERSION_REPAIRS,
    TIME_ZONE,
    WINDOW_END_DAYS_BACK,
)

if TYPE_CHECKING:
    from . import CkwConfigEntry
    from .coordinator import CkwData

__all__ = ["RepairBaselineStore", "async_check_repairs"]

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: The repair store of every loaded entry, kept for the entry's lifetime so two
#: checks cannot overwrite each other's in-memory copy. Dropped when it unloads.
_BASELINES: dict[str, RepairBaselineStore] = {}


class RepairBaselineStore:
    """What the repair checks last saw of one entry, across restarts.

    It holds one value per delivery point: the ``supply_start_date`` the
    delivery point was first seen with. The baseline is deliberately **not**
    moved when it differs from the current value - a meter exchange stays
    visible after a restart instead of being swallowed by it - and it is stored
    next to nothing else, so the file stays a few bytes.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the store of one entry; nothing is read until ``async_load``."""
        self._key = f"{STORAGE_KEY_REPAIRS}.{entry_id}"
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION_REPAIRS, self._key)
        self._data: dict[str, Any] = {}
        self._loaded = False

    @property
    def key(self) -> str:
        """The storage key, for the tests and the debug log."""
        return self._key

    async def async_load(self) -> None:
        """Read the file once; every later call is a no-op."""
        if self._loaded:
            return
        self._data = await self._store.async_load() or {}
        self._loaded = True

    def supply_start(self, deliverypoint: str) -> str | None:
        """Return the ``supply_start_date`` this delivery point was first seen with."""
        meters = self._data.get("supply_start")
        if not isinstance(meters, Mapping):
            return None
        value = meters.get(deliverypoint)
        return value if isinstance(value, str) else None

    async def async_remember_supply_start(self, deliverypoint: str, supply_start: str) -> None:
        """Record a delivery point's first ``supply_start_date`` and save it."""
        meters = self._data.get("supply_start")
        if not isinstance(meters, dict):
            meters = {}
            self._data["supply_start"] = meters
        if meters.get(deliverypoint) == supply_start:
            return
        meters[deliverypoint] = supply_start
        await self._store.async_save(self._data)


async def async_check_repairs(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    data: CkwData,
) -> None:
    """Raise and clear the repair issues of one entry after a successful poll.

    Called by the coordinator on every poll, so each condition is re-evaluated
    with fresh data: an issue appears on the poll that first sees the problem
    and disappears on the poll that sees it gone.
    """
    store = _baseline(hass, entry)
    await store.async_load()
    await _async_check_meter_exchanged(hass, entry, data, store)
    _async_check_data_quality(hass, entry, data)
    _async_check_stale_publication(hass, entry, data)
    _async_check_quarter_hour_retention(hass, entry)


# ---------------------------------------------------------------------------
# 1. A meter was exchanged under an existing statistic id
# ---------------------------------------------------------------------------


async def _async_check_meter_exchanged(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    data: CkwData,
    store: RepairBaselineStore,
) -> None:
    """Report a delivery point that came back with a new ``supply_start_date``.

    The comparison is against the date the delivery point was **first** seen
    with, not against the previous poll: a value that changed while Home
    Assistant was off is still a change, and a value CKW corrects back to the
    original clears the issue again. A delivery point whose first sight is
    recorded here raises nothing, because without a baseline there is nothing to
    compare with - the history may simply predate this integration (#124).
    """
    deliverypoint = _deliverypoint(entry)
    meter = data.meter_info.get(deliverypoint)
    supply_start = None if meter is None else meter.supply_start
    if supply_start is None:
        # CKW sent no supply start date for this meter: nothing to compare.
        _async_delete_issue(hass, entry, ISSUE_METER_EXCHANGED)
        return

    baseline = store.supply_start(deliverypoint)
    if baseline is None:
        await store.async_remember_supply_start(deliverypoint, supply_start)
        _async_delete_issue(hass, entry, ISSUE_METER_EXCHANGED)
        return
    if baseline == supply_start:
        _async_delete_issue(hass, entry, ISSUE_METER_EXCHANGED)
        return

    _async_raise_issue(
        hass,
        entry,
        ISSUE_METER_EXCHANGED,
        placeholders={
            "deliverypoint": mask_deliverypoint(deliverypoint),
            "date": supply_start,
        },
    )


# ---------------------------------------------------------------------------
# 2. CKW keeps flagging intervals
# ---------------------------------------------------------------------------


@callback
def _async_check_data_quality(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    data: CkwData,
) -> None:
    """Report more than ``DATA_QUALITY_FLAG_INTERVALS`` flagged intervals.

    The count is limited to the rolling window the coordinator polls, so an
    issue from months ago does not keep the repair dashboard occupied after the
    flags stopped (#149, ``docs/explanation/data-model.md``).
    """
    deliverypoint = _deliverypoint(entry)
    now = data.polled_at
    flagged = 0 if now is None else _flagged_intervals(entry, deliverypoint, now)
    if flagged <= DATA_QUALITY_FLAG_INTERVALS:
        _async_delete_issue(hass, entry, ISSUE_DATA_QUALITY)
        return

    _async_raise_issue(
        hass,
        entry,
        ISSUE_DATA_QUALITY,
        placeholders={
            "deliverypoint": mask_deliverypoint(deliverypoint),
            "count": str(flagged),
            "days": str(ROLLING_WINDOW_DAYS),
        },
    )


def _flagged_intervals(entry: CkwConfigEntry, deliverypoint: str, now: datetime) -> int:
    """Count the quality-flagged intervals of the rolling window."""
    start = _rolling_window_start(now)
    flagged = 0
    for measurement in entry.runtime_data.coordinator.intervals(deliverypoint):
        day = _local_day(measurement)
        if day is not None and day >= start and measurement.has_quality_issue:
            flagged += 1
    return flagged


def _rolling_window_start(now: datetime) -> date:
    """Return the first local day of the window the coordinator polls."""
    end = now.astimezone(TIME_ZONE).date() - timedelta(days=WINDOW_END_DAYS_BACK)
    return end - timedelta(days=ROLLING_WINDOW_DAYS - 1)


def _local_day(measurement: CkwMeasurement) -> date | None:
    """Return the local calendar day an interval covers, or ``None``."""
    if measurement.start_utc is not None:
        return measurement.start_utc.astimezone(TIME_ZONE).date()
    if measurement.end_utc is not None:
        # An interval that ends at local midnight belongs to the day before.
        return (measurement.end_utc - timedelta(microseconds=1)).astimezone(TIME_ZONE).date()
    return None


# ---------------------------------------------------------------------------
# 3. CKW stopped publishing
# ---------------------------------------------------------------------------


@callback
def _async_check_stale_publication(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    data: CkwData,
) -> None:
    """Report a newest interval that is older than the stall threshold.

    The age of the newest published interval is the same measurement the
    coordinator's stall warning uses (``PUBLICATION_STALL_DAYS``), read from the
    data instead of from the coordinator's wall clock, so the check survives a
    restart and needs no memory of its own. The normal spread is 1-3 days
    (ADR-0004).
    """
    deliverypoint = _deliverypoint(entry)
    totals = data.meters.get(deliverypoint)
    newest = None if totals is None else totals.last_interval_end
    now = data.polled_at
    if newest is None or now is None:
        _async_delete_issue(hass, entry, ISSUE_STALE_PUBLICATION)
        return
    if now - newest <= timedelta(days=PUBLICATION_STALL_DAYS):
        _async_delete_issue(hass, entry, ISSUE_STALE_PUBLICATION)
        return

    _async_raise_issue(
        hass,
        entry,
        ISSUE_STALE_PUBLICATION,
        placeholders={
            "deliverypoint": mask_deliverypoint(deliverypoint),
            "date": newest.astimezone(TIME_ZONE).date().isoformat(),
            "days": str(PUBLICATION_STALL_DAYS),
        },
    )


# ---------------------------------------------------------------------------
# 4. The quarter-hour history would be purged early
# ---------------------------------------------------------------------------


@callback
def _async_check_quarter_hour_retention(hass: HomeAssistant, entry: CkwConfigEntry) -> None:
    """Report an enabled quarter-hour sensor whose history is purged too early.

    The sensor writes entity states, never statistics (ADR-0009), so its history
    lives exactly as long as ``recorder.purge_keep_days`` allows. Only a user who
    enabled the sensor cares, so the issue is raised for an enabled entity only;
    a sensor that is not registered yet (the first poll of a setup, before the
    platform forwarded) counts as disabled.
    """
    deliverypoint = _deliverypoint(entry)
    if not _quarter_hour_enabled(hass, entry, deliverypoint):
        _async_delete_issue(hass, entry, ISSUE_QUARTER_HOUR_RETENTION)
        return

    keep_days = _purge_keep_days(hass)
    if keep_days is None:
        # The recorder is not set up (or does not expose its retention), so the
        # value is unknown. Guessing a default would raise an issue about a
        # setting this integration does not own; skip the check instead.
        _async_delete_issue(hass, entry, ISSUE_QUARTER_HOUR_RETENTION)
        return
    if keep_days >= QUARTER_HOUR_MIN_RETENTION_DAYS:
        _async_delete_issue(hass, entry, ISSUE_QUARTER_HOUR_RETENTION)
        return

    _async_raise_issue(
        hass,
        entry,
        ISSUE_QUARTER_HOUR_RETENTION,
        placeholders={
            "deliverypoint": mask_deliverypoint(deliverypoint),
            "days": str(keep_days),
            "minimum": str(QUARTER_HOUR_MIN_RETENTION_DAYS),
        },
        learn_more_url=DOCS_QUARTER_HOUR_RETENTION_URL,
    )


@callback
def _quarter_hour_enabled(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    deliverypoint: str,
) -> bool:
    """Whether this entry's quarter-hour sensor exists and is enabled."""
    unique_id = f"{entry.entry_id}_{deliverypoint}_{QUARTER_HOUR_ENTITY_KEY}"
    registered = er.async_get(hass).async_get_entity_id(Platform.SENSOR, DOMAIN, unique_id)
    if registered is None:
        return False
    entity = er.async_get(hass).async_get(registered)
    return entity is not None and not entity.disabled


def _purge_keep_days(hass: HomeAssistant) -> int | None:
    """Return the recorder's effective retention, or ``None`` when unreadable.

    ``recorder.purge_keep_days`` is not part of ``hass.config``: the recorder
    setup stores the instance in ``hass.data`` and the value lives on it. When
    that instance is absent - Home Assistant runs without a recorder, or this
    check runs before it started - the retention is unknown and the caller skips
    the issue rather than assuming a number.
    """
    if "recorder" not in hass.config.components:
        return None

    recorder = hass.data.get(DATA_INSTANCE)
    keep_days = getattr(recorder, "keep_days", None)
    if isinstance(keep_days, bool) or not isinstance(keep_days, int):
        return None
    return keep_days


# ---------------------------------------------------------------------------
# The issue registry: one raise path, one delete path
# ---------------------------------------------------------------------------


def _issue_id(entry: CkwConfigEntry, condition: str) -> str:
    """Return the namespaced issue id that keeps two accounts apart."""
    return f"{DOMAIN}_{entry.entry_id}_{condition}"


def _deliverypoint(entry: CkwConfigEntry) -> str:
    """Return the delivery point this config entry reads."""
    return entry.runtime_data.coordinator.deliverypoint


@callback
def _async_raise_issue(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    condition: str,
    *,
    placeholders: Mapping[str, str],
    learn_more_url: str | None = None,
) -> None:
    """Create the issue of ``condition``, unless it is already in the UI.

    The registry is checked first: re-creating a present issue only fires
    another update event and would rewrite the text the user is reading, so a
    present issue is left exactly as it is.
    """
    registry = ir.async_get(hass)
    issue_id = _issue_id(entry, condition)
    if registry.async_get_issue(DOMAIN, issue_id) is not None:
        return
    registry.async_get_or_create(
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=True,
        learn_more_url=learn_more_url,
        severity=IssueSeverity.WARNING,
        translation_key=condition,
        translation_placeholders=dict(placeholders),
    )
    _LOGGER.debug(
        "raising the %s repair issue for %s",
        condition,
        mask_deliverypoint(_deliverypoint(entry)),
    )


@callback
def _async_delete_issue(hass: HomeAssistant, entry: CkwConfigEntry, condition: str) -> None:
    """Delete the issue of ``condition`` if it is present."""
    registry = ir.async_get(hass)
    issue_id = _issue_id(entry, condition)
    if registry.async_get_issue(DOMAIN, issue_id) is None:
        return
    registry.async_delete(DOMAIN, issue_id)
    _LOGGER.debug(
        "clearing the %s repair issue for %s",
        condition,
        mask_deliverypoint(_deliverypoint(entry)),
    )


def _baseline(hass: HomeAssistant, entry: CkwConfigEntry) -> RepairBaselineStore:
    """Return the entry's repair store, creating and caching it on first use."""
    store = _BASELINES.get(entry.entry_id)
    if store is None:
        store = RepairBaselineStore(hass, entry.entry_id)
        _BASELINES[entry.entry_id] = store
        entry.async_on_unload(partial(_forget_baseline, entry.entry_id))
    return store


@callback
def _forget_baseline(entry_id: str) -> None:
    """Drop the cached repair store of an entry that unloads."""
    _BASELINES.pop(entry_id, None)
