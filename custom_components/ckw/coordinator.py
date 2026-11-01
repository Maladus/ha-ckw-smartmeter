"""The one coordinator that owns every CKW request of a config entry.

Home Assistant's ``DataUpdateCoordinator`` refreshes on the interval the options
flow stores, and ``_async_update_data`` is the only code in the integration that
talks to CKW: no entity, no platform and no service opens a session of its own
(``docs/reference/conventions.md``, "Polling and caching").

What a poll does:

1. Ask for the rolling window, ending yesterday in ``Europe/Zurich`` - today's
   aggregate does not exist yet (ADR-0004). The dates handed to the client are
   local dates, the timestamps it returns are UTC.
2. Pace the request against ``x-ratelimit-*``: when the previous response said
   the quota is exhausted, wait for the reported reset instead of firing into a
   ``429`` (#147). A ``429`` that still arrives fails the poll with the reset
   time in the message; it is never retried in a loop.
3. Merge the intervals into the per-meter values already seen. Duplicates are
   ignored, a correction replaces the earlier value, and only a genuinely new
   interval counts as published data.
4. Recompute the cumulative totals per delivery point and log what moved
   (#149). The publication ``INFO`` line is written for the delivery point of
   this config entry only, so one entry sees exactly one line per published day.

The exceptions of ``api.py`` become Home Assistant exceptions here: an expired
sign-in becomes ``ConfigEntryAuthFailed`` (reauth, no retry), a rate limit and
every transport failure become ``UpdateFailed``, and a window CKW calls too big
is retried once with half of it. Those ``UpdateFailed`` objects carry the same
``translation_domain``/``translation_key`` pair the service errors in
``exceptions.py`` use, so no English message is written here (#130); the
mapping itself is documented in ``docs/reference/conventions.md`` ("Error
mapping", #70).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CkwApiError,
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwCustomer,
    CkwError,
    CkwMeasurement,
    CkwMeter,
    CkwMeterMeasurements,
    CkwRangeTooBigError,
    CkwRateLimitError,
    RateLimitInfo,
    mask_deliverypoint,
)
from .const import (
    CONF_METER,
    CONF_POLL_HOURS,
    DAYS_AGGREGATION,
    DEFAULT_POLL_HOURS,
    DOMAIN,
    MAX_POLL_HOURS,
    MIN_POLL_HOURS,
    MIN_WINDOW_DAYS,
    MINUTES_AGGREGATION,
    NAME,
    PUBLICATION_LAG_WARNING_HOURS,
    PUBLICATION_STALL_DAYS,
    QUARTER_HOUR_MINUTES,
    RATE_LIMIT_MIN_REMAINING,
    REAUTH_STATUSES,
    ROLLING_WINDOW_DAYS,
    SECONDS_PER_HOUR,
    TIME_ZONE,
    WINDOW_END_DAYS_BACK,
)
from .exceptions import format_reset_seconds

__all__ = ["CkwCoordinator", "CkwData", "MeterTotals", "QuarterHourReading"]

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MeterTotals:
    """Cumulative values of one delivery point across every interval seen.

    ``kwh_*`` sum the billed quantities of the intervals that carry no quality
    flag; an interval flagged by ``anzahl_linien_*`` above ``1`` is counted in
    ``quality_intervals`` instead, because ``data-model.md`` forbids silently
    summing it. ``last_interval_end`` is the newest UTC ``zeitstempel_bis_utc``
    and ``publication_delay_h`` the hours between it and the poll, i.e. how old
    the newest published day was when Home Assistant read it.
    """

    kwh_total: float
    kwh_ht: float
    kwh_nt: float
    last_interval_end: datetime | None
    publication_delay_h: float | None
    intervals_seen: int
    quality_intervals: int
    #: The newest complete day, for ``consumption_yesterday``. It is the newest
    #: interval even when CKW published it a day or two late, because that is
    #: the last complete day the API knows about.
    last_day_kwh: float | None = None
    last_day_start: datetime | None = None


@dataclass(frozen=True, slots=True)
class QuarterHourReading:
    """The newest 15-minute interval of one delivery point (#152).

    ``kwh`` is ``None`` when the interval is incomplete or quality-flagged; the
    sensor then has no number rather than a wrong one. The timestamps stay UTC,
    the sensor presents them as they are.
    """

    kwh: float | None
    interval_start: datetime | None
    interval_end: datetime | None
    publication_delay_h: float | None


@dataclass(slots=True)
class CkwData:
    """What one poll produced, as every entity reads it from the coordinator."""

    meters: dict[str, MeterTotals]
    rate_limit: RateLimitInfo | None
    newest_interval: datetime | None
    #: The customer's meters, keyed by delivery point. The model and type here
    #: are what the per-meter device and entity names show.
    meter_info: dict[str, CkwMeter] = field(default_factory=dict)
    #: Newest quarter-hour interval per delivery point, when one was fetched.
    quarter_hour: dict[str, QuarterHourReading] = field(default_factory=dict)
    #: When this successful poll happened, for the diagnostic timestamp sensor.
    polled_at: datetime | None = None


class CkwCoordinator(DataUpdateCoordinator[CkwData]):
    """Own every CKW request of one config entry and merge its window.

    The entry is passed to the base class so the coordinator registers its own
    shutdown as an entry unload callback: the refresh timer cannot outlive the
    entry. ``always_update=False`` keeps listeners quiet for an unchanged poll,
    which is the common case - CKW publishes once a day.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: CkwClient) -> None:
        """Build the coordinator for one entry, reading its poll interval."""
        self.client = client
        self.deliverypoint = str(entry.data.get(CONF_METER, ""))
        self.poll_hours = _poll_hours(entry.options.get(CONF_POLL_HOURS, DEFAULT_POLL_HOURS))
        # Every interval already merged, per delivery point. Keeping the values
        # (not only their timestamps) makes a correction a replacement instead
        # of a second addition, and keeps the totals recomputable.
        self._intervals: dict[str, dict[datetime, CkwMeasurement]] = {}
        # Newest interval of the entry's delivery point that was already logged
        # as published, and when it last advanced (wall clock, UTC).
        self._published_newest: datetime | None = None
        self._last_advance: datetime | None = None
        self._lag_warned = False
        self._stall_warned = False
        self._new_intervals: dict[str, int] = {}
        # The customer's meters, refreshed on every poll so a new delivery point
        # can be discovered without editing ids (#134).
        self._meter_info: dict[str, CkwMeter] = {}
        # The rate-limit headers of the latest response and when they arrived,
        # so a reset that already elapsed does not turn into a stale sleep.
        self._rate_limit_info: RateLimitInfo | None = None
        self._rate_limit_at = 0.0
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=NAME,
            update_interval=timedelta(hours=self.poll_hours),
            always_update=False,
        )

    @property
    def new_intervals(self) -> Mapping[str, int]:
        """Intervals added by the most recent poll, per delivery point."""
        return self._new_intervals

    def intervals(self, deliverypoint: str) -> tuple[CkwMeasurement, ...]:
        """Return every daily interval merged for one delivery point, oldest first.

        The statistics import reads the same values the sensors total, so a day
        the poll already knows is imported once and not fetched a second time.
        """
        return tuple(
            measurement
            for _end, measurement in sorted(self._intervals.get(deliverypoint, {}).items())
        )

    async def async_pace(self) -> None:
        """Wait for the next rate-limit window when the quota is exhausted.

        Public because the statistics backfill sends its own requests through
        the same client and must pace them like a poll does (#147).
        """
        await self._async_pace()

    async def _async_update_data(self) -> CkwData:
        """Fetch, merge and log one poll, mapping failures as documented."""
        try:
            return await self._async_collect()
        except HomeAssistantError as err:
            self._log_failure(err)
            raise

    async def _async_collect(self) -> CkwData:
        """Run one poll: fetch the window, merge it, log what changed."""
        now_local = dt_util.now(TIME_ZONE)
        now = now_local.astimezone(UTC)
        end = now_local.date() - timedelta(days=WINDOW_END_DAYS_BACK)
        days = ROLLING_WINDOW_DAYS
        start = _window_start(end, days)

        customer = await self._async_fetch_customer()
        if customer is not None:
            self._meter_info = _meter_info(customer)

        try:
            series = await self._async_fetch(start, end)
        except CkwRangeTooBigError as err:
            days //= 2
            if days < MIN_WINDOW_DAYS:
                raise _range_too_big_failure() from err
            _LOGGER.warning(
                "CKW rejected the %d-day window as too big, retrying once with the last %d days",
                ROLLING_WINDOW_DAYS,
                days,
            )
            start = _window_start(end, days)
            try:
                series = await self._async_fetch(start, end)
            except CkwRangeTooBigError as retry_err:
                raise _range_too_big_failure() from retry_err

        new_counts, corrected, newest = self._merge(series)
        self._new_intervals = new_counts

        totals = {
            deliverypoint: _totals_for(intervals, now)
            for deliverypoint, intervals in self._intervals.items()
        }
        for deliverypoint, meter_totals in totals.items():
            if meter_totals.quality_intervals:
                _LOGGER.debug(
                    "delivery point %s: %d flagged interval(s) kept out of the totals",
                    mask_deliverypoint(deliverypoint),
                    meter_totals.quality_intervals,
                )

        entry_totals = totals.get(self.deliverypoint)
        if entry_totals is not None:
            self._log_publication(entry_totals, now)

        meter_info = dict(self._meter_info)
        for deliverypoint in totals:
            meter_info.setdefault(deliverypoint, _placeholder_meter(deliverypoint))

        quarter_hour: dict[str, QuarterHourReading] = {}
        if customer is not None:
            quarter_hour = await self._async_fetch_quarter_hour(now)

        _log_poll(
            start, end, series, newest, now, changed=bool(corrected) or any(new_counts.values())
        )

        return CkwData(
            meters=totals,
            rate_limit=self.client.last_rate_limit,
            newest_interval=newest,
            meter_info=meter_info,
            quarter_hour=quarter_hour,
            polled_at=now,
        )

    async def _async_fetch(self, start: date, end: date) -> list[CkwMeterMeasurements]:
        """Pace, request one window and translate ``api.py`` exceptions."""
        await self._async_pace()
        try:
            return await self.client.async_get_measurements(
                start, end, aggregation=DAYS_AGGREGATION
            )
        except CkwAuthError as err:
            raise ConfigEntryAuthFailed(
                f"CKW rejected the sign-in ({err}); reauthentication is required"
            ) from err
        except CkwRateLimitError as err:
            raise _rate_limit_failure(err) from err
        except CkwRangeTooBigError:
            # The caller halves the window once; it is not a failure by itself.
            raise
        except CkwApiError as err:
            raise _api_failure(err) from err
        except CkwConnectionError as err:
            raise _connection_failure() from err
        except CkwError as err:
            raise _unknown_failure() from err
        except aiohttp.ClientResponseError as err:
            if err.status in REAUTH_STATUSES:
                raise ConfigEntryAuthFailed(
                    f"the CKW sign-in could not be refreshed ({err.status}); "
                    "reauthentication is required"
                ) from err
            raise _connection_failure() from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise _connection_failure() from err
        finally:
            # ``Reset`` counts forward from this response, not from the next
            # poll: stamp it so ``_async_pace`` can subtract the elapsed time.
            self._rate_limit_info = self.client.last_rate_limit
            self._rate_limit_at = time.monotonic()

    async def _async_fetch_customer(self) -> CkwCustomer | None:
        """Fetch the customer's meter list, or keep the previous one on failure.

        The sensor platform needs the meter model for the device page, but the
        daily measurements are the poll's real job. A failure here is therefore
        logged at debug and does not fail the poll; an expired sign-in fails
        the measurement request immediately afterwards anyway.
        """
        await self._async_pace()
        try:
            return await self.client.async_get_customer()
        except Exception as err:  # noqa: BLE001 - metadata must not break the data poll
            _LOGGER.debug(
                "cannot read the CKW meter list for %s, keeping the last known meters: %s",
                mask_deliverypoint(self.deliverypoint),
                err,
            )
            return None

    async def _async_fetch_quarter_hour(self, now: datetime) -> dict[str, QuarterHourReading]:
        """Fetch today's quarter-hour series and keep its newest interval (#152).

        CKW publishes quarter-hour rows for the current local day; only the
        newest complete 15-minute interval becomes the sensor state, and the
        full series is never imported as statistics (ADR-0009). A failed
        quarter-hour request leaves the daily sensors untouched.
        """
        day = now.astimezone(TIME_ZONE).date()
        await self._async_pace()
        try:
            series = await self.client.async_get_measurements(
                day, day, aggregation=MINUTES_AGGREGATION
            )
        except Exception as err:  # noqa: BLE001 - the daily data still stands
            _LOGGER.debug(
                "the CKW quarter-hour request for %s failed, leaving the sensor empty: %s",
                day.isoformat(),
                err,
            )
            return {}

        readings: dict[str, QuarterHourReading] = {}
        for meter_series in series:
            deliverypoint = meter_series.deliverypointnumber
            if not deliverypoint:
                continue
            newest = _newest_quarter_hour(meter_series.measurements)
            if newest is None:
                continue
            end = _measurement_end(newest)
            delay = None if end is None else (now - end).total_seconds() / SECONDS_PER_HOUR
            readings[deliverypoint] = QuarterHourReading(
                kwh=None if newest.has_quality_issue else newest.kwh_total,
                interval_start=newest.start_utc,
                interval_end=newest.end_utc,
                publication_delay_h=delay,
            )
        return readings

    async def _async_pace(self) -> None:
        """Wait for the next rate-limit window when the quota is exhausted."""
        info = self.client.last_rate_limit
        if info is None or info.remaining is None:
            return
        if info.remaining >= RATE_LIMIT_MIN_REMAINING:
            return
        delay = info.reset_seconds
        if delay is None or delay <= 0:
            _LOGGER.debug(
                "the rate-limit quota is exhausted but no reset time was reported; sending anyway"
            )
            return
        if info is self._rate_limit_info:
            delay -= time.monotonic() - self._rate_limit_at
        if delay <= 0:
            _LOGGER.debug("the rate-limit window has already reset; sending the request")
            return
        _LOGGER.debug(
            "the rate-limit quota is exhausted, waiting %.1f s for the next window", delay
        )
        await asyncio.sleep(delay)

    def _merge(
        self, series: list[CkwMeterMeasurements]
    ) -> tuple[dict[str, int], int, datetime | None]:
        """Merge one response, returning new counts, corrections and the newest end."""
        new_counts: dict[str, int] = {}
        corrected = 0
        newest: datetime | None = None
        for meter_series in series:
            deliverypoint = meter_series.deliverypointnumber
            if not deliverypoint:
                _LOGGER.debug("ignoring a measurement series without a delivery point")
                continue
            known = self._intervals.setdefault(deliverypoint, {})
            new = 0
            for measurement in meter_series.measurements:
                end = measurement.end_utc or measurement.start_utc
                if end is None:
                    _LOGGER.debug(
                        "ignoring an interval of %s without a timestamp",
                        mask_deliverypoint(deliverypoint),
                    )
                    continue
                if newest is None or end > newest:
                    newest = end
                previous = known.get(end)
                if previous is None:
                    new += 1
                elif previous == measurement:
                    continue
                else:
                    corrected += 1
                    _LOGGER.debug(
                        "CKW corrected the interval ending %s of %s",
                        end.isoformat(),
                        mask_deliverypoint(deliverypoint),
                    )
                known[end] = measurement
            new_counts[deliverypoint] = new
        return new_counts, corrected, newest

    def _log_failure(self, err: HomeAssistantError) -> None:
        """Log a failed poll once at warning, repeated failures at debug (#72).

        The base coordinator reads ``last_update_success`` *after*
        ``_async_update_data`` raised and logs the same failure again at error
        level when it was still ``True``. Marking the poll failed here keeps one
        line per failure at the level the conventions ask for; the base's
        recovery line stays the one INFO line when data flows again.

        During the first refresh the config entry is still being set up and
        Home Assistant reports the retry itself ("not ready yet; retrying"), so
        the line is debug there instead of one warning per setup attempt.
        """
        first_failure = self.last_update_success
        self.last_update_success = False
        setting_up = (
            self.config_entry is not None
            and self.config_entry.state is ConfigEntryState.SETUP_IN_PROGRESS
        )
        if first_failure and not setting_up:
            _LOGGER.warning(
                "the CKW poll for %s failed, retrying at the next interval: %s",
                mask_deliverypoint(self.deliverypoint),
                err,
            )
        else:
            _LOGGER.debug(
                "the CKW poll for %s failed: %s",
                mask_deliverypoint(self.deliverypoint),
                err,
            )

    def _log_publication(self, totals: MeterTotals, now: datetime) -> None:
        """Log a newly published day once, and warn about a lagging feed."""
        newest = totals.last_interval_end
        if newest is None:
            return

        previous = self._published_newest
        if previous is None:
            # The first poll only establishes the baseline: nothing "advanced".
            self._published_newest = newest
            self._last_advance = now
        elif newest > previous:
            self._published_newest = newest
            self._last_advance = now
            _LOGGER.info(
                "new grid data published: %s, lag %.1f h (%s)",
                _interval_day(newest, self._intervals.get(self.deliverypoint, {})),
                totals.publication_delay_h or 0.0,
                mask_deliverypoint(self.deliverypoint),
            )

        self._warn_publication(totals, now)

    def _warn_publication(self, totals: MeterTotals, now: datetime) -> None:
        """Warn once when the lag or the stall crosses its threshold (#149)."""
        lag = totals.publication_delay_h
        if lag is not None and lag > PUBLICATION_LAG_WARNING_HOURS:
            if not self._lag_warned:
                self._lag_warned = True
                _LOGGER.warning(
                    "the newest CKW interval for %s is %.0f h old, more than the expected %d h: "
                    "CKW has not published grid data yet",
                    mask_deliverypoint(self.deliverypoint),
                    lag,
                    PUBLICATION_LAG_WARNING_HOURS,
                )
        else:
            self._lag_warned = False

        stalled = self._last_advance is not None and now - self._last_advance >= timedelta(
            days=PUBLICATION_STALL_DAYS
        )
        if stalled:
            if not self._stall_warned:
                self._stall_warned = True
                _LOGGER.warning(
                    "the newest CKW interval for %s has not advanced for %d days; "
                    "check whether CKW stopped publishing",
                    mask_deliverypoint(self.deliverypoint),
                    PUBLICATION_STALL_DAYS,
                )
        else:
            self._stall_warned = False


def _poll_hours(raw: object) -> int:
    """Return a usable poll interval, falling back to the default outside the bounds."""
    if (
        isinstance(raw, int)
        and not isinstance(raw, bool)
        and MIN_POLL_HOURS <= raw <= MAX_POLL_HOURS
    ):
        return raw
    _LOGGER.debug(
        "ignoring the poll interval %r outside %d-%d h, using %d h",
        raw,
        MIN_POLL_HOURS,
        MAX_POLL_HOURS,
        DEFAULT_POLL_HOURS,
    )
    return DEFAULT_POLL_HOURS


def _window_start(end: date, days: int) -> date:
    """Return the first day of a window of ``days`` local days ending on ``end``."""
    return end - timedelta(days=max(days, MIN_WINDOW_DAYS) - 1)


def _range_too_big_failure() -> UpdateFailed:
    """Build the ``UpdateFailed`` for a window CKW still calls too big (Gold #130)."""
    return UpdateFailed(translation_domain=DOMAIN, translation_key="range_too_big")


def _api_failure(err: CkwApiError) -> UpdateFailed:
    """Build the ``UpdateFailed`` for a status CKW answered with (Gold #130)."""
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"status": str(err.status)},
    )


def _connection_failure() -> UpdateFailed:
    """Build the ``UpdateFailed`` for an unreachable CKW API (Gold #130)."""
    return UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect")


def _unknown_failure() -> UpdateFailed:
    """Build the ``UpdateFailed`` for a failure without a specific key (Gold #130)."""
    return UpdateFailed(translation_domain=DOMAIN, translation_key="unknown")


def _rate_limit_failure(err: CkwRateLimitError) -> UpdateFailed:
    """Build the ``UpdateFailed`` that names the reset time of a ``429``."""
    reset = err.retry_after_seconds
    if reset is None or reset <= 0:
        return UpdateFailed(translation_domain=DOMAIN, translation_key="rate_limited_no_reset")
    # The base coordinator may bring the next poll forward by the reset, but
    # only once: nothing here retries inside the same poll.
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="rate_limited",
        translation_placeholders={"reset": format_reset_seconds(reset)},
        retry_after=reset,
    )


def _totals_for(intervals: Mapping[datetime, CkwMeasurement], now: datetime) -> MeterTotals:
    """Recompute one delivery point's cumulative totals and its publication delay."""
    kwh_total = 0.0
    kwh_ht = 0.0
    kwh_nt = 0.0
    quality = 0
    last_end: datetime | None = None
    last_measurement: CkwMeasurement | None = None
    for end, measurement in sorted(intervals.items()):
        last_end = end
        last_measurement = measurement
        if measurement.has_quality_issue:
            quality += 1
            continue
        if measurement.kwh_ht is None or measurement.kwh_nt is None:
            # Unknown is never free electricity: an incomplete interval is
            # skipped rather than summed as zero (data-model.md).
            continue
        kwh_ht += measurement.kwh_ht
        kwh_nt += measurement.kwh_nt
        kwh_total += measurement.kwh_ht + measurement.kwh_nt

    delay: float | None = None
    if last_end is not None:
        delay = (now - last_end).total_seconds() / SECONDS_PER_HOUR

    last_day_kwh: float | None = None
    last_day_start: datetime | None = None
    if last_measurement is not None:
        last_day_start = last_measurement.start_utc
        if not last_measurement.has_quality_issue:
            last_day_kwh = last_measurement.kwh_total

    return MeterTotals(
        kwh_total=kwh_total,
        kwh_ht=kwh_ht,
        kwh_nt=kwh_nt,
        last_interval_end=last_end,
        publication_delay_h=delay,
        intervals_seen=len(intervals),
        quality_intervals=quality,
        last_day_kwh=last_day_kwh,
        last_day_start=last_day_start,
    )


def _measurement_end(measurement: CkwMeasurement) -> datetime | None:
    """Return the end of an interval, falling back to its start."""
    return measurement.end_utc or measurement.start_utc


def _newest_quarter_hour(
    measurements: tuple[CkwMeasurement, ...],
) -> CkwMeasurement | None:
    """Return the newest 15-minute interval, ignoring wider rows."""
    newest: CkwMeasurement | None = None
    newest_end: datetime | None = None
    for measurement in measurements:
        end = _measurement_end(measurement)
        if end is None:
            continue
        start = measurement.start_utc
        if start is not None and end - start > timedelta(minutes=QUARTER_HOUR_MINUTES):
            continue
        if newest_end is None or end > newest_end:
            newest = measurement
            newest_end = end
    return newest


def _meter_info(customer: CkwCustomer) -> dict[str, CkwMeter]:
    """Return the customer's meters, keyed by delivery point.

    Gas meters are kept here so the sensor platform can recognise and skip
    them; they share the measurement payload but are outside this product.
    """
    meters: dict[str, CkwMeter] = {}
    for location in customer.locations:
        for meter in location.meters:
            if not meter.deliverypointnumber:
                continue
            meters.setdefault(meter.deliverypointnumber, meter)
    return meters


def _placeholder_meter(deliverypoint: str) -> CkwMeter:
    """Build the metadata stand-in for a meter the customer payload omitted."""
    return CkwMeter(
        id=None,
        model=None,
        type=None,
        fuel=None,
        deliverypointnumber=deliverypoint,
        supply_start=None,
        supply_end=None,
    )


def _interval_day(end: datetime, intervals: Mapping[datetime, CkwMeasurement]) -> str:
    """Return the local calendar day an interval covers, for the INFO line."""
    measurement = intervals.get(end)
    start = end if measurement is None or measurement.start_utc is None else measurement.start_utc
    return start.astimezone(TIME_ZONE).date().isoformat()


def _log_poll(
    start: date,
    end: date,
    series: list[CkwMeterMeasurements],
    newest: datetime | None,
    now: datetime,
    *,
    changed: bool,
) -> None:
    """Write the per-poll DEBUG line: window, counts, newest stamp, lag, change."""
    interval_count = sum(len(item.measurements) for item in series)
    lag = None if newest is None else (now - newest).total_seconds() / SECONDS_PER_HOUR
    _LOGGER.debug(
        "poll: window %s..%s, %d interval(s) from %d delivery point(s), newest %s, lag %s h, "
        "changed: %s",
        start.isoformat(),
        end.isoformat(),
        interval_count,
        len(series),
        "none" if newest is None else newest.isoformat(),
        "unknown" if lag is None else f"{lag:.1f}",
        "yes" if changed else "no",
    )
