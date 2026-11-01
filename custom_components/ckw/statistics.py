"""Long-term statistics for the Energy dashboard, and the backfill that fills it.

CKW serves one billed aggregate per delivery point and local day. The Energy
dashboard needs years of it, so the daily values are imported as **hourly
entity statistics** (ADR-0002, ADR-0009): the recorder refuses any row whose
timestamp is not on the top of the hour, so every row this module writes starts
at local midnight in ``Europe/Zurich`` - one bucket per local day, 23 h or 25 h
of wall time on a DST day (``docs/explanation/quarter-hour-history.md``).

Three properties are not optional:

* **Idempotent.** The cumulative ``sum`` of a window is always recomputed, never
  appended, so re-importing an overlapping window overwrites the same rows with
  the same numbers (#85). The sums are anchored on the newest imported day, and
  that anchor is persisted, so a restart continues the series instead of
  restarting it (#86, #89).
* **Cheap when nothing changed.** The rows of every import are hashed and the
  hash is stored; a poll that sees the same window and the same values writes
  nothing (#89).
* **Paced and resumable.** The backfill walks the history in chunks, pauses
  between them and stores the offset of the first day it has not imported yet,
  so a restart or a revoked sign-in continues where it stopped (#86, #147).

Nothing here imports sub-hourly rows, and no cost statistic is written: the CHF
fields stay parsed and unbilled (decision A2).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Final

import aiohttp
import voluptuous as vol
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import (
    CkwAuthError,
    CkwError,
    CkwMeasurement,
    CkwMeterMeasurements,
    mask_deliverypoint,
)
from .const import (
    ATTR_DAYS,
    ATTR_ENTRY_ID,
    ATTR_FORCE,
    BACKFILL_CHUNK_DAYS,
    BACKFILL_DAYS,
    BACKFILL_PAUSE_SECONDS,
    DAYS_AGGREGATION,
    DOMAIN,
    MAX_BACKFILL_DAYS,
    REAUTH_STATUSES,
    SERVICE_BACKFILL,
    STATISTIC_ENTITY_KEY,
    STATISTIC_SOURCE,
    STATISTIC_UNIT,
    STATISTIC_UNIT_CLASS,
    STORAGE_KEY_STATISTICS,
    STORAGE_VERSION_STATISTICS,
    TIME_ZONE,
    WINDOW_END_DAYS_BACK,
    Fuel,
)
from .exceptions import (
    CkwAuthenticationError,
    CkwConnectionFailedError,
    CkwNoDataError,
    CkwNoMetersError,
    error_for_api_failure,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import ServiceCall

    from . import CkwConfigEntry
    from .coordinator import CkwCoordinator

__all__ = [
    "BACKFILL_SCHEMA",
    "BackfillProgress",
    "MeterState",
    "StatisticsBucket",
    "StatisticsImport",
    "StatisticsState",
    "StatisticsStore",
    "async_backfill",
    "async_import_meter_statistics",
    "async_import_polled_statistics",
    "async_prepare_statistics",
    "async_register_statistics",
    "async_register_backfill_service",
]

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: Decimals the checksum keeps of every value. Floats from an unrelated code
#: path differ far below this, so a rounded hash never imports twice.
_CHECKSUM_DECIMALS: Final[int] = 6

#: One store per entry, kept for the entry's lifetime so two writers cannot
#: overwrite each other's in-memory copy. Dropped when the entry unloads.
_STORES: dict[str, StatisticsStore] = {}


@dataclass(frozen=True, slots=True)
class StatisticsBucket:
    """One hour-aligned statistics row before the cumulative sum is added.

    ``start`` is local midnight with the ``Europe/Zurich`` zone attached, so the
    hour rule of the recorder holds by construction and the day boundaries stay
    ours (``docs/reference/conventions.md``, "Time").
    """

    start: datetime
    kwh: float


@dataclass(frozen=True, slots=True)
class MeterState:
    """The sum anchor of one delivery point and what was imported last.

    ``anchor_start``/``anchor_sum`` are the start and the cumulative sum of the
    newest imported day. Every import recomputes its sums from that anchor,
    which is what turns an overlapping re-import into an overwrite instead of a
    second addition (ADR-0002, #85). ``checksum`` is the hash of the rows the
    last import computed, so an unchanged window writes nothing (#89).
    """

    anchor_start: datetime
    anchor_sum: float
    checksum: str


@dataclass(frozen=True, slots=True)
class StatisticsImport:
    """What one import did, for the logs and the tests."""

    statistic_id: str
    #: Rows handed to the recorder.
    rows: int
    #: Whether a recorder write happened at all (``False`` means the checksum matched).
    written: bool
    #: Intervals left out because they carry no usable quantity.
    skipped: int
    #: Cumulative sum of the newest row, or ``None`` when nothing was computed.
    last_sum: float | None


@dataclass(slots=True)
class StatisticsState:
    """Backfill progress of one entry, published as ``entry.runtime_data``.

    ``done`` says the initial backfill needs no more run, ``next_start`` is the
    first local day it has not imported yet and ``chunks_done``/``chunks_total``
    are the progress the log line reports. ``running`` guards against a second
    backfill while one is in flight.
    """

    done: bool = False
    running: bool = False
    days: int = BACKFILL_DAYS
    next_start: date | None = None
    chunks_done: int = 0
    chunks_total: int = 0


@dataclass(frozen=True, slots=True)
class BackfillProgress:
    """The persisted chunk offset of one backfill job."""

    days: int
    window_start: date
    next_start: date
    chunks_done: int
    done: bool


class StatisticsStore:
    """The state a restart needs to continue the statistics import.

    One file per config entry: the sum anchor of every delivery point and the
    chunk offset of the backfill. It holds no history - the recorder owns that -
    only the bookkeeping that makes the import idempotent across restarts
    (ADR-0009 rejects an integration-owned history store).
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the store of one entry; nothing is read until ``async_load``."""
        self._key = f"{STORAGE_KEY_STATISTICS}.{entry_id}"
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION_STATISTICS, self._key)
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

    async def async_save(self) -> None:
        """Write the state to disk now, not on a delayed save."""
        await self._store.async_save(self._data)

    def meter_state(self, meter_key: str) -> MeterState | None:
        """Return the stored anchor of one delivery point, if there is one."""
        meters = self._data.get("meters")
        if not isinstance(meters, Mapping):
            return None
        return _parse_meter_state(meters.get(meter_key))

    def set_meter_state(self, meter_key: str, state: MeterState) -> None:
        """Store the anchor of one delivery point."""
        meters = self._data.get("meters")
        if not isinstance(meters, dict):
            meters = {}
            self._data["meters"] = meters
        meters[meter_key] = {
            "anchor_start": state.anchor_start.isoformat(),
            "anchor_sum": state.anchor_sum,
            "checksum": state.checksum,
        }

    def clear_meters(self) -> None:
        """Drop every anchor, so the next import restarts the cumulative sum."""
        self._data.pop("meters", None)

    @property
    def backfill(self) -> BackfillProgress | None:
        """The stored chunk offset of the backfill, if one ever ran."""
        return _parse_backfill(self._data.get("backfill"))

    def set_backfill(self, progress: BackfillProgress) -> None:
        """Store the chunk offset of the backfill."""
        self._data["backfill"] = {
            "days": progress.days,
            "window_start": progress.window_start.isoformat(),
            "next_start": progress.next_start.isoformat(),
            "chunks_done": progress.chunks_done,
            "done": progress.done,
        }


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


async def async_import_meter_statistics(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    meter_key: str,
    entity_id: str,
    intervals: Iterable[CkwMeasurement],
    *,
    force: bool = False,
) -> StatisticsImport:
    """Import the daily aggregates of one delivery point as hourly statistics.

    The intervals are aggregated into one bucket per local day (``days`` is
    already daily, but a DST day stays exactly one bucket), the cumulative
    ``sum`` is recomputed from the persisted anchor, and the rows are written
    with ``source="recorder"`` under ``entity_id`` as the statistic id, so the
    Energy dashboard can select the sensor (ADR-0002).

    An interval whose quantities are missing or quality-flagged is skipped and
    logged: unknown is never imported as zero, and a gap in the series is more
    honest than a fabricated reading (``docs/explanation/data-model.md``).

    ``force`` ignores the stored anchor and the checksum, which is what repairs
    a recorder that lost the rows of this statistic.
    """
    buckets, skipped = _daily_buckets(intervals)
    if not buckets:
        _LOGGER.debug(
            "statistics: no importable day for %s (%d interval(s) skipped)",
            mask_deliverypoint(meter_key),
            skipped,
        )
        return StatisticsImport(
            statistic_id=entity_id, rows=0, written=False, skipped=skipped, last_sum=None
        )

    store = _statistics_store(hass, entry)
    await store.async_load()
    previous = None if force else store.meter_state(meter_key)

    rows, anchor = _cumulative_rows(buckets, previous)
    if previous is not None and previous.checksum == anchor.checksum:
        _LOGGER.debug(
            "statistics: %d row(s) of %s unchanged (%s..%s), skipping the import",
            len(rows),
            mask_deliverypoint(meter_key),
            rows[0]["start"].isoformat(),
            rows[-1]["start"].isoformat(),
        )
        return StatisticsImport(
            statistic_id=entity_id,
            rows=0,
            written=False,
            skipped=skipped,
            last_sum=anchor.anchor_sum,
        )

    async_import_statistics(hass, _metadata(entity_id), rows)
    store.set_meter_state(meter_key, anchor)
    await store.async_save()
    _LOGGER.debug(
        "statistics: imported %d row(s) for %s (%s..%s), cumulative sum %.3f kWh",
        len(rows),
        mask_deliverypoint(meter_key),
        rows[0]["start"].isoformat(),
        rows[-1]["start"].isoformat(),
        anchor.anchor_sum,
    )
    return StatisticsImport(
        statistic_id=entity_id,
        rows=len(rows),
        written=True,
        skipped=skipped,
        last_sum=anchor.anchor_sum,
    )


async def async_import_polled_statistics(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
) -> None:
    """Import the rolling window the coordinator holds after a successful poll.

    ADR-0002 asks for the 7-day window on every poll, so a day CKW publishes
    late, or corrects, is imported when it shows up and overwrites the earlier
    row. The checksum makes the common case - nothing changed - write nothing.
    """
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    if data is None:
        return
    for deliverypoint in sorted(data.meters):
        if not _is_electricity(coordinator, deliverypoint):
            continue
        entity_id = _statistic_entity_id(hass, entry, deliverypoint)
        if entity_id is None:
            _LOGGER.debug(
                "statistics: no total sensor for %s yet, skipping its import",
                mask_deliverypoint(deliverypoint),
            )
            continue
        intervals = coordinator.intervals(deliverypoint)
        if not intervals:
            continue
        await async_import_meter_statistics(hass, entry, deliverypoint, entity_id, intervals)


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------


async def async_backfill(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    coordinator: CkwCoordinator,
    days: int = BACKFILL_DAYS,
    *,
    force: bool = False,
    raise_on_error: bool = False,
) -> None:
    """Import the last ``days`` local days of history, chunk by chunk.

    The job walks the window from its oldest day towards yesterday, so the
    cumulative sum grows forward and every chunk continues the previous one.
    A chunk is ``BACKFILL_CHUNK_DAYS`` wide, well below the server's range cap,
    and the loop pauses between chunks so a 730-day job stays inside the quota
    (#147).

    The offset of the first day that is not imported yet is stored after every
    chunk, so a restart resumes instead of starting over. A revoked sign-in
    stops the job and starts reauthentication; it is never retried here (#86).

    ``raise_on_error`` is for the ``ckw.backfill`` service: the user asked for
    the import, so a failure is raised as a translated ``HomeAssistantError``
    instead of only being logged and resumed at the next setup (#130).
    """
    state = entry.runtime_data.statistics_state
    if state.running:
        _LOGGER.warning(
            "backfill: one is already running for %s, not starting a second",
            entry.title,
        )
        return

    store = _statistics_store(hass, entry)
    await store.async_load()

    today = dt_util.now(TIME_ZONE).date()
    end = today - timedelta(days=WINDOW_END_DAYS_BACK)
    start = end - timedelta(days=max(days, 1) - 1)
    total_chunks = max(1, math.ceil(max(days, 1) / BACKFILL_CHUNK_DAYS))
    cursor, chunks_done = _resume_point(store.backfill, start, force=force)

    if force:
        # A meter swap or a suspected anchor: rebuild the series from the first
        # day of this window instead of continuing a sum nobody trusts.
        store.clear_meters()

    state.running = True
    state.done = False
    state.days = max(days, 1)
    state.chunks_done = chunks_done
    state.chunks_total = total_chunks
    state.next_start = cursor
    _LOGGER.info(
        "backfill: importing %d day(s) of statistics %s..%s for %s (%d chunk(s) of %d days)",
        max(days, 1),
        start.isoformat(),
        end.isoformat(),
        entry.title,
        total_chunks,
        BACKFILL_CHUNK_DAYS,
    )

    imported = 0
    try:
        while cursor <= end:
            chunk_start = cursor
            chunk_end = min(chunk_start + timedelta(days=BACKFILL_CHUNK_DAYS - 1), end)
            rows = await _async_import_chunk(
                hass, entry, coordinator, chunk_start, chunk_end, raise_on_error=raise_on_error
            )
            if rows is None:
                # Abort-safe: keep the stored offset so the next setup, or the
                # `ckw.backfill` service, continues here instead of restarting.
                _LOGGER.warning(
                    "backfill: stopped at %s, %d/%d chunk(s) done; the offset is kept",
                    chunk_start.isoformat(),
                    chunks_done,
                    total_chunks,
                )
                return

            imported += rows
            chunks_done += 1
            cursor = chunk_end + timedelta(days=1)
            finished = cursor > end
            store.set_backfill(
                BackfillProgress(
                    days=max(days, 1),
                    window_start=start,
                    next_start=cursor,
                    chunks_done=chunks_done,
                    done=finished,
                )
            )
            await store.async_save()
            state.chunks_done = chunks_done
            state.next_start = None if finished else cursor
            _LOGGER.info(
                "backfill: %s..%s done, %d/%d",
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                chunks_done,
                total_chunks,
            )
            if not finished:
                await asyncio.sleep(BACKFILL_PAUSE_SECONDS)

        state.done = True
        if raise_on_error and imported == 0:
            # The service asked for history and could import none of it: saying
            # so is more useful than reporting a silent success (#130).
            raise CkwNoDataError()
        _LOGGER.info(
            "backfill: finished %d/%d chunk(s) for %s, %d row(s) imported",
            chunks_done,
            total_chunks,
            entry.title,
            imported,
        )
    finally:
        state.running = False


def _resume_point(
    progress: BackfillProgress | None,
    start: date,
    *,
    force: bool,
) -> tuple[date, int]:
    """Return the first day to import and the chunks already done.

    A finished job, a forced one, or a request wider than the stored window
    starts at ``start``; a pending job whose window still covers ``start``
    continues at its stored offset (#86).
    """
    if force or progress is None or progress.done or progress.window_start > start:
        return start, 0
    return max(progress.next_start, start), progress.chunks_done


async def _async_import_chunk(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    coordinator: CkwCoordinator,
    start: date,
    end: date,
    *,
    raise_on_error: bool = False,
) -> int | None:
    """Fetch one chunk and import every delivery point in it, or ``None`` on failure.

    A background backfill logs a failure and keeps its offset (``None``). The
    service sets ``raise_on_error``, so the same condition reaches the user as a
    translated ``HomeAssistantError`` instead of only the log (#130).
    """
    await coordinator.async_pace()
    try:
        series = await coordinator.client.async_get_measurements(
            start, end, aggregation=DAYS_AGGREGATION
        )
    except aiohttp.ClientResponseError as err:
        # The OAuth2 token refresh answers with a bare response error, not a
        # ``CkwError``; a dead chain asks for a new sign-in like a 401 does.
        if err.status in REAUTH_STATUSES:
            _LOGGER.error(
                "backfill: the CKW sign-in could not be refreshed (%s); "
                "reauthentication is required",
                err.status,
            )
            entry.async_start_reauth(hass)
            if raise_on_error:
                raise CkwAuthenticationError() from err
            return None
        _LOGGER.warning(
            "backfill: the chunk %s..%s failed, the next run resumes at %s: %s",
            start.isoformat(),
            end.isoformat(),
            start.isoformat(),
            err,
        )
        if raise_on_error:
            raise CkwConnectionFailedError() from err
        return None
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.warning(
            "backfill: the chunk %s..%s failed, the next run resumes at %s: %s",
            start.isoformat(),
            end.isoformat(),
            start.isoformat(),
            err,
        )
        if raise_on_error:
            raise CkwConnectionFailedError() from err
        return None
    except CkwAuthError as err:
        # A dead sign-in is not retried: the backfill stops and the user is
        # asked to sign in again (ADR-0007).
        _LOGGER.error("backfill: CKW rejected the sign-in (%s); reauthentication is required", err)
        entry.async_start_reauth(hass)
        if raise_on_error:
            raise CkwAuthenticationError() from err
        return None
    except CkwError as err:
        _LOGGER.warning(
            "backfill: the chunk %s..%s failed, the next run resumes at %s: %s",
            start.isoformat(),
            end.isoformat(),
            start.isoformat(),
            err,
        )
        if raise_on_error:
            raise error_for_api_failure(err) from err
        return None

    meters = _electricity_series(coordinator, series)
    if not meters:
        # An empty window is normal (the meter was not installed yet), but data
        # that belongs to no electricity meter is a condition the service names.
        if raise_on_error and series and not _has_electricity_series(coordinator, series):
            raise CkwNoMetersError()
        return 0

    imported = 0
    skipped = 0
    for meter_series in meters:
        entity_id = _statistic_entity_id(hass, entry, meter_series.deliverypointnumber)
        if entity_id is None:
            _LOGGER.debug(
                "statistics: no total sensor for %s yet, skipping its import",
                mask_deliverypoint(meter_series.deliverypointnumber),
            )
            skipped += 1
            continue
        result = await async_import_meter_statistics(
            hass,
            entry,
            meter_series.deliverypointnumber,
            entity_id,
            meter_series.measurements,
        )
        imported += result.rows

    if skipped == len(meters):
        # Delivery points arrived but none has a sensor: importing would import
        # nothing, so stop here and retry when the entities exist.
        _LOGGER.warning(
            "statistics: none of the %d delivery point(s) of %s..%s has a total sensor yet; "
            "stopping so the next run retries this chunk",
            len(meters),
            start.isoformat(),
            end.isoformat(),
        )
        if raise_on_error:
            raise CkwNoDataError()
        return None
    return imported


# ---------------------------------------------------------------------------
# Setup: the poll listener, the runtime state and the service
# ---------------------------------------------------------------------------


async def async_prepare_statistics(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
) -> StatisticsState:
    """Load the persisted state and publish it as ``entry.runtime_data``."""
    store = _statistics_store(hass, entry)
    await store.async_load()
    state = entry.runtime_data.statistics_state
    progress = store.backfill
    if progress is not None:
        state.done = progress.done
        state.days = progress.days
        state.chunks_done = progress.chunks_done
        state.chunks_total = max(1, math.ceil(progress.days / BACKFILL_CHUNK_DAYS))
        state.next_start = None if progress.done else progress.next_start
    progress_text = (
        ""
        if state.next_start is None
        else (
            f", next day {state.next_start.isoformat()} ({state.chunks_done}/{state.chunks_total})"
        )
    )
    _LOGGER.debug(
        "statistics: %s state for %s is %s%s",
        store.key,
        entry.title,
        "done" if state.done else "pending",
        progress_text,
    )
    return state


@callback
def async_register_statistics(hass: HomeAssistant, entry: CkwConfigEntry) -> None:
    """Re-import the rolling window after every successful poll (ADR-0002).

    The import runs in a background task: the listener is a callback and must
    not block the coordinator. While a backfill is running the days are imported
    by the backfill itself, so nothing is written twice.
    """
    coordinator = entry.runtime_data.coordinator

    @callback
    def _handle_refresh() -> None:
        if entry.runtime_data.statistics_state.running:
            return
        entry.async_create_background_task(
            hass,
            async_import_polled_statistics(hass, entry),
            name="ckw statistics import",
        )

    entry.async_on_unload(coordinator.async_add_listener(_handle_refresh))


@callback
def async_register_backfill_service(hass: HomeAssistant) -> None:
    """Register ``ckw.backfill`` once for the whole domain (#123).

    This is the user-facing way to repair history after a meter swap or a
    recorder wipe: it re-runs the import for one entry, or for every loaded one,
    with the requested number of days.
    """

    async def _async_handle_backfill(call: ServiceCall) -> None:
        days = int(call.data.get(ATTR_DAYS, BACKFILL_DAYS))
        force = bool(call.data.get(ATTR_FORCE, False))
        for entry in _backfill_targets(hass, call.data.get(ATTR_ENTRY_ID)):
            await async_backfill(
                hass,
                entry,
                entry.runtime_data.coordinator,
                days,
                force=force,
                raise_on_error=True,
            )

    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL,
        _async_handle_backfill,
        schema=BACKFILL_SCHEMA,
    )


#: ``days`` is bounded so a typo cannot ask CKW for a decade of data.
BACKFILL_SCHEMA: Final = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Optional(ATTR_DAYS): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_BACKFILL_DAYS)),
        vol.Optional(ATTR_FORCE, default=False): bool,
    }
)


def _backfill_targets(hass: HomeAssistant, entry_id: object) -> list[ConfigEntry]:
    """Return the loaded CKW entries a service call should repair."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if isinstance(entry_id, str) and entry_id:
        matched = [entry for entry in entries if entry.entry_id == entry_id]
        if not matched:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entry",
                translation_placeholders={"entry_id": entry_id},
            )
        entries = matched
    loaded = [
        entry
        for entry in entries
        if entry.state is ConfigEntryState.LOADED and hasattr(entry, "runtime_data")
    ]
    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_loaded_entry",
        )
    return loaded


# ---------------------------------------------------------------------------
# Buckets, sums and the recorder metadata
# ---------------------------------------------------------------------------


def _daily_buckets(
    intervals: Iterable[CkwMeasurement],
) -> tuple[list[StatisticsBucket], int]:
    """Aggregate the daily intervals into one bucket per local day.

    The API's ``days`` aggregation is already daily, but the bucket is derived
    from the interval's local start date instead of trusting that: a DST day
    therefore yields exactly one bucket (23 h or 25 h of wall time), and two
    intervals that fall on the same local day are summed into that day (#88).

    Returns the buckets, oldest first, and how many intervals were skipped.
    """
    totals: dict[datetime, float] = {}
    skipped = 0
    for measurement in intervals:
        day = _local_day(measurement)
        if day is None:
            _LOGGER.debug("statistics: skipping an interval without a timestamp")
            skipped += 1
            continue
        if measurement.has_quality_issue:
            # data-model.md: a flagged interval is not summed silently.
            _LOGGER.debug("statistics: skipping the flagged interval of %s", day.isoformat())
            skipped += 1
            continue
        kwh = measurement.kwh_total
        if kwh is None:
            _LOGGER.debug(
                "statistics: skipping the interval of %s without a billed quantity",
                day.isoformat(),
            )
            skipped += 1
            continue
        start = _local_midnight(day)
        if not _hour_aligned(start):
            _LOGGER.warning(
                "statistics: refusing the bucket %s of %s, it is not on the top of the hour",
                start.isoformat(),
                day.isoformat(),
            )
            skipped += 1
            continue
        totals[start] = totals.get(start, 0.0) + kwh
    buckets = [StatisticsBucket(start=start, kwh=kwh) for start, kwh in sorted(totals.items())]
    return buckets, skipped


def _cumulative_rows(
    buckets: Sequence[StatisticsBucket],
    previous: MeterState | None,
) -> tuple[list[StatisticData], MeterState]:
    """Recompute the cumulative ``sum`` of every bucket from the stored anchor.

    Three cases, and each keeps the rows the recorder already holds untouched:

    * **overlap** - the window covers the anchor: walk back from it, so the
      days imported before keep exactly the sums they have (#85).
    * **newer** - the window starts after the anchor: continue from it. A day
      CKW never published contributes nothing, so the sum simply carries on.
    * **older** - the whole window predates the anchor: the days in between are
      unknown here, so the series restarts at the oldest row of this window and
      the following chunks rebuild it forward.

    The new anchor is the newest row of the import, and the hash of the rows is
    what the next import compares against to skip an unchanged window (#89).
    """
    first, last = buckets[0].start, buckets[-1].start
    base = 0.0
    if previous is not None:
        if last < previous.anchor_start:
            _LOGGER.info(
                "statistics: importing history older than the stored sum (%s < %s); "
                "the cumulative sum restarts at the first re-imported day",
                last.isoformat(),
                previous.anchor_start.isoformat(),
            )
        elif first <= previous.anchor_start:
            base = previous.anchor_sum - sum(
                bucket.kwh for bucket in buckets if bucket.start <= previous.anchor_start
            )
        else:
            if first > previous.anchor_start + timedelta(days=1):
                _LOGGER.debug(
                    "statistics: the window starts %s, after the imported %s; "
                    "the cumulative sum continues from the anchor",
                    first.isoformat(),
                    previous.anchor_start.isoformat(),
                )
            base = previous.anchor_sum

    rows: list[StatisticData] = []
    running = base
    for bucket in buckets:
        running += bucket.kwh
        rows.append(StatisticData(start=bucket.start, state=bucket.kwh, sum=running))
    return rows, MeterState(anchor_start=last, anchor_sum=running, checksum=_checksum(rows))


def _metadata(entity_id: str) -> StatisticMetaData:
    """Return the metadata of an entity statistic the Energy dashboard can pick."""
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source=STATISTIC_SOURCE,
        statistic_id=entity_id,
        unit_class=STATISTIC_UNIT_CLASS,
        unit_of_measurement=STATISTIC_UNIT,
    )


def _checksum(rows: Sequence[StatisticData]) -> str:
    """Hash the rows of one import, rounded so float noise cannot differ."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row["start"].isoformat().encode("utf-8"))
        digest.update(b"|")
        digest.update(
            f"{row['state']:.{_CHECKSUM_DECIMALS}f}|{row['sum']:.{_CHECKSUM_DECIMALS}f}".encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


@callback
def _forget_store(entry_id: str) -> None:
    """Drop the cached store of an entry that unloads."""
    _STORES.pop(entry_id, None)


def _statistics_store(hass: HomeAssistant, entry: CkwConfigEntry) -> StatisticsStore:
    """Return the entry's store, creating and caching it on first use."""
    store = _STORES.get(entry.entry_id)
    if store is None:
        store = StatisticsStore(hass, entry.entry_id)
        _STORES[entry.entry_id] = store
        entry.async_on_unload(partial(_forget_store, entry.entry_id))
    return store


def _statistic_entity_id(
    hass: HomeAssistant, entry: CkwConfigEntry, deliverypoint: str
) -> str | None:
    """Return the entity id that becomes the statistic id of one delivery point.

    The statistic id is the entity id of the total sensor, whose unique id is
    ``f"{entry_id}_{deliverypointnumber}_consumption_total"`` (ADR-0003), so a
    rename in the UI does not split the history (ADR-0002).
    """
    unique_id = f"{entry.entry_id}_{deliverypoint}_{STATISTIC_ENTITY_KEY}"
    return er.async_get(hass).async_get_entity_id(Platform.SENSOR, DOMAIN, unique_id)


def _is_electricity(coordinator: CkwCoordinator, deliverypoint: str) -> bool:
    """Whether this delivery point is an electricity meter (gas is out of scope)."""
    data = coordinator.data
    meter = None if data is None else data.meter_info.get(deliverypoint)
    return meter is None or meter.fuel in (None, Fuel.ELEC)


def _electricity_series(
    coordinator: CkwCoordinator, series: Sequence[CkwMeterMeasurements]
) -> list[CkwMeterMeasurements]:
    """Return the series of the electricity delivery points only."""
    return [
        meter_series
        for meter_series in series
        if meter_series.deliverypointnumber
        and meter_series.measurements
        and _is_electricity(coordinator, meter_series.deliverypointnumber)
    ]


def _has_electricity_series(
    coordinator: CkwCoordinator, series: Sequence[CkwMeterMeasurements]
) -> bool:
    """Whether the response named at least one electricity delivery point."""
    return any(
        meter_series.deliverypointnumber
        and _is_electricity(coordinator, meter_series.deliverypointnumber)
        for meter_series in series
    )


def _local_day(measurement: CkwMeasurement) -> date | None:
    """Return the local calendar day an interval covers, or ``None``."""
    if measurement.start_utc is not None:
        return measurement.start_utc.astimezone(TIME_ZONE).date()
    if measurement.end_utc is not None:
        # An interval that ends at local midnight belongs to the day before.
        return (measurement.end_utc - timedelta(microseconds=1)).astimezone(TIME_ZONE).date()
    return None


def _local_midnight(day: date) -> datetime:
    """Return the start of a local day in ``Europe/Zurich``."""
    return datetime.combine(day, time.min, tzinfo=TIME_ZONE)


def _hour_aligned(start: datetime) -> bool:
    """Whether the recorder accepts this timestamp: minutes and seconds are zero."""
    return start.minute == 0 and start.second == 0 and start.microsecond == 0


def _parse_meter_state(raw: object) -> MeterState | None:
    """Read a stored anchor, tolerating a file written by an older version."""
    if not isinstance(raw, Mapping):
        return None
    start = raw.get("anchor_start")
    total = raw.get("anchor_sum")
    checksum = raw.get("checksum")
    if not isinstance(start, str) or not isinstance(total, int | float):
        return None
    if isinstance(total, bool) or not isinstance(checksum, str):
        return None
    try:
        anchor_start = dt_util.parse_datetime(start)
    except ValueError:
        return None
    if anchor_start is None:
        return None
    return MeterState(
        anchor_start=dt_util.as_utc(anchor_start),
        anchor_sum=float(total),
        checksum=checksum,
    )


def _parse_backfill(raw: object) -> BackfillProgress | None:
    """Read a stored chunk offset, tolerating a file written by an older version."""
    if not isinstance(raw, Mapping):
        return None
    days = raw.get("days")
    window_start = raw.get("window_start")
    next_start = raw.get("next_start")
    chunks_done = raw.get("chunks_done")
    done = raw.get("done")
    if not isinstance(days, int) or isinstance(days, bool):
        return None
    if not isinstance(chunks_done, int) or isinstance(chunks_done, bool):
        return None
    if not isinstance(window_start, str) or not isinstance(next_start, str):
        return None
    if not isinstance(done, bool):
        return None
    try:
        window_day = date.fromisoformat(window_start)
        next_day = date.fromisoformat(next_start)
    except ValueError:
        return None
    return BackfillProgress(
        days=days,
        window_start=window_day,
        next_start=next_day,
        chunks_done=chunks_done,
        done=done,
    )
