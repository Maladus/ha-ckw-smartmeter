"""Tests for the long-term statistics import and the resumable backfill (#84-#89).

The recorder is real (``recorder_mock`` provides an in-memory instance), so the
rows are read back through the recorder's own query path: that is the only place
where the hour rule of ``async_import_statistics`` is enforced, and where an
append instead of an overwrite would show up as a doubled sum.

No socket is opened: the backfill's client is a fake that returns the daily
intervals of the requested window, and ``asyncio.sleep`` is patched so the
rate-limit pauses are asserted instead of waited for.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, Final
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
import voluptuous as vol
from freezegun import freeze_time
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ckw import statistics as statistics_module
from custom_components.ckw.api import (
    CkwAuthError,
    CkwClient,
    CkwCustomer,
    CkwError,
    CkwMeasurement,
    CkwMeterMeasurements,
)
from custom_components.ckw.const import (
    ATTR_DAYS,
    ATTR_ENTRY_ID,
    ATTR_FORCE,
    BACKFILL_CHUNK_DAYS,
    BACKFILL_DAYS,
    BACKFILL_PAUSE_SECONDS,
    CONF_CITY,
    CONF_CUSTOMER_ID,
    CONF_METER,
    DAYS_AGGREGATION,
    DOMAIN,
    ROLLING_WINDOW_DAYS,
    SERVICE_BACKFILL,
    STATISTIC_ENTITY_KEY,
    TIME_ZONE,
    WINDOW_END_DAYS_BACK,
    Fuel,
)
from custom_components.ckw.statistics import (
    BackfillProgress,
    MeterState,
    StatisticsBucket,
    StatisticsState,
    _async_import_chunk,
    _backfill_targets,
    _cumulative_rows,
    _daily_buckets,
    _local_day,
    _parse_backfill,
    _parse_meter_state,
    async_backfill,
    async_import_meter_statistics,
    async_import_polled_statistics,
    async_register_backfill_service,
)

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")

METER: Final = "CH0000000000000000000000000000001"
STATISTIC_ID: Final = "sensor.ckw_test_consumption_total"
#: Frozen "now" for the backfill: winter (CET), so the local day is unambiguous.
FROZEN_NOW: Final = datetime(2025, 6, 15, 12, 0, tzinfo=TIME_ZONE)


# ---------------------------------------------------------------------------
# Intervals as CKW returns them
# ---------------------------------------------------------------------------


def _midnight(day: date) -> datetime:
    """Return local midnight of ``day`` in ``Europe/Zurich``."""
    return datetime.combine(day, time.min, tzinfo=TIME_ZONE)


def _daily(
    day: date,
    *,
    kwh_ht: float | None = 2.0,
    kwh_nt: float | None = 1.0,
    lines_p: int | None = 1,
) -> CkwMeasurement:
    """Build one daily interval, local midnight to local midnight."""
    return CkwMeasurement(
        start_utc=_midnight(day).astimezone(UTC),
        end_utc=_midnight(day + timedelta(days=1)).astimezone(UTC),
        kwh_ht=kwh_ht,
        kwh_nt=kwh_nt,
        chf_ht=None,
        chf_nt=None,
        lines_p=lines_p,
        lines_fw=1,
        lines_fb=1,
    )


def _days(start: date, count: int) -> list[date]:
    """Return ``count`` consecutive days from ``start``."""
    return [start + timedelta(days=offset) for offset in range(count)]


# ---------------------------------------------------------------------------
# Fixtures: an entry, its runtime data and a reading helper
# ---------------------------------------------------------------------------


class FakeClient:
    """Stands in for ``CkwClient.async_get_measurements``; no socket is opened."""

    def __init__(self) -> None:
        self.calls: list[tuple[date, date, str]] = []
        self.error: Exception | None = None

    async def async_get_measurements(
        self,
        start: date,
        end: date,
        aggregation: str = DAYS_AGGREGATION,
    ) -> list[CkwMeterMeasurements]:
        self.calls.append((start, end, aggregation))
        if self.error is not None:
            raise self.error
        measurements = tuple(_daily(day) for day in _days(start, (end - start).days + 1))
        return [CkwMeterMeasurements(deliverypointnumber=METER, measurements=measurements)]


class FakeCoordinator:
    """The two coordinator members the backfill uses."""

    def __init__(self) -> None:
        self.client = FakeClient()
        self.data = SimpleNamespace(meters={}, meter_info={})
        self.paced = 0
        self.interval_data: Sequence[CkwMeasurement] = ()

    async def async_pace(self) -> None:
        """Count the pacing calls instead of waiting for a quota window."""
        self.paced += 1

    def intervals(self, _deliverypoint: str) -> Sequence[CkwMeasurement]:
        """Return the window a test configured, empty by default."""
        return self.interval_data

    def async_add_listener(self, listener: Any) -> Any:
        """Remember the poll listener and return a no-op unsubscribe."""
        self.listener = listener
        return lambda: None


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a config entry whose runtime data carries the statistics state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
        title="CKW test",
        unique_id="statistics-test",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        api=None,
        coordinator=FakeCoordinator(),
        statistics_state=StatisticsState(),
    )
    return entry


@pytest.fixture
def statistic_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Register the total sensor entity the statistic id is derived from."""
    entity = er.async_get(hass).async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{entry.entry_id}_{METER}_{STATISTIC_ENTITY_KEY}",
        suggested_object_id="ckw_test_consumption_total",
    )
    assert entity.entity_id == STATISTIC_ID
    return entity.entity_id


async def read_rows(
    hass: HomeAssistant,
    statistic_id: str = STATISTIC_ID,
) -> list[Any]:
    """Read the stored hourly rows back through the recorder's query path."""
    await async_wait_recording_done(hass)
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2030, 1, 1, tzinfo=UTC),
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return rows.get(statistic_id, [])


def row_start(row: Any) -> datetime:
    """Return a stored row's start as an aware UTC datetime."""
    return dt_util.utc_from_timestamp(row["start"])


def local_start(row: Any) -> datetime:
    """Return a stored row's start in ``Europe/Zurich``."""
    return row_start(row).astimezone(TIME_ZONE)


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


async def test_import_writes_hourly_cumulative_rows(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Rows start at local midnight, carry the daily kWh and a growing sum."""
    intervals = [_daily(day) for day in _days(date(2025, 5, 1), 3)]

    result = await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, intervals)

    assert result.written is True
    assert result.rows == 3
    assert result.skipped == 0
    assert result.statistic_id == STATISTIC_ID

    rows = await read_rows(hass)
    assert len(rows) == 3
    for row in rows:
        start = local_start(row)
        assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert [row["state"] for row in rows] == [3.0, 3.0, 3.0]
    assert [row["sum"] for row in rows] == [3.0, 6.0, 9.0]
    assert result.last_sum == 9.0


async def test_the_statistic_is_an_entity_statistic(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The import is bound to the entity, so the Energy dashboard can pick it."""
    from homeassistant.components.recorder.models import StatisticMetaData

    metadata: StatisticMetaData = statistics_module._metadata(STATISTIC_ID)

    assert metadata["statistic_id"] == STATISTIC_ID
    assert metadata["source"] == "recorder"
    assert metadata["has_sum"] is True
    assert metadata["unit_of_measurement"] == "kWh"
    assert metadata["unit_class"] == "energy"


async def test_importing_the_same_window_twice_is_idempotent(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The second import skips on the checksum and leaves rows and sum alone."""
    intervals = [_daily(day) for day in _days(date(2025, 5, 1), 4)]

    first = await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, intervals)
    rows_before = await read_rows(hass)

    second = await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, intervals)
    rows_after = await read_rows(hass)

    assert first.written is True
    assert second.written is False
    assert len(rows_after) == len(rows_before) == 4
    assert rows_after[-1]["sum"] == pytest.approx(rows_before[-1]["sum"])
    assert [row["sum"] for row in rows_after] == [row["sum"] for row in rows_before]


async def test_a_forced_import_overwrites_instead_of_appending(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """``force`` writes the same rows again; the sum is not added twice."""
    intervals = [_daily(day) for day in _days(date(2025, 5, 1), 3)]

    await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, intervals)
    rows_before = await read_rows(hass)

    forced = await async_import_meter_statistics(
        hass, entry, METER, STATISTIC_ID, intervals, force=True
    )
    rows_after = await read_rows(hass)

    assert forced.written is True
    assert len(rows_after) == len(rows_before) == 3
    assert [row["sum"] for row in rows_after] == [row["sum"] for row in rows_before]
    assert rows_after[-1]["sum"] == pytest.approx(9.0)


async def test_an_overlapping_window_keeps_the_same_sums(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Re-importing a shifted window recomputes the overlapping rows identically."""
    week = [_daily(day) for day in _days(date(2025, 5, 1), 7)]
    await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, week)
    rows_before = await read_rows(hass)

    # The rolling window of the next poll: the last five days only.
    result = await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, week[-5:])
    rows_after = await read_rows(hass)

    assert result.written is True
    assert len(rows_after) == 7
    assert [row["sum"] for row in rows_after] == [row["sum"] for row in rows_before]
    assert rows_after[-1]["sum"] == pytest.approx(21.0)


async def test_a_dst_day_yields_exactly_one_bucket(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """23 h and 25 h local days each produce one row, 24/23/25 h apart in UTC."""
    spring = _days(date(2025, 3, 29), 3)
    autumn = _days(date(2025, 10, 25), 3)
    intervals = [_daily(day) for day in [*spring, *autumn]]

    result = await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, intervals)

    assert result.rows == 6
    rows = await read_rows(hass)
    assert len(rows) == 6
    # One bucket per local day, even though 2025-03-30 is 23 h and 2025-10-26 is 25 h.
    assert {local_start(row).date() for row in rows} == {*spring, *autumn}
    starts = [row_start(row) for row in rows]
    assert starts[1] - starts[0] == timedelta(hours=24)
    assert starts[2] - starts[1] == timedelta(hours=23)
    assert starts[4] - starts[3] == timedelta(hours=24)
    assert starts[5] - starts[4] == timedelta(hours=25)


async def test_an_interval_without_a_quantity_is_skipped(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Unknown and flagged intervals are left out; later sums still add up."""
    first, missing, flagged, last = _days(date(2025, 5, 1), 4)
    intervals: Sequence[CkwMeasurement] = [
        _daily(first),
        _daily(missing, kwh_nt=None),
        _daily(flagged, lines_p=2),
        _daily(last),
    ]

    result = await async_import_meter_statistics(hass, entry, METER, STATISTIC_ID, intervals)

    assert result.skipped == 2
    assert result.rows == 2
    rows = await read_rows(hass)
    assert len(rows) == 2
    assert [local_start(row).date() for row in rows] == [first, last]
    assert [row["sum"] for row in rows] == [3.0, 6.0]


async def test_an_import_without_any_usable_day_writes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """All intervals unknown means no row, not a row of zero."""
    result = await async_import_meter_statistics(
        hass,
        entry,
        METER,
        STATISTIC_ID,
        [_daily(date(2025, 5, 1), kwh_ht=None, kwh_nt=None)],
    )

    assert result.written is False
    assert result.rows == 0
    assert result.skipped == 1
    assert await read_rows(hass) == []


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------


async def test_backfill_walks_the_chunks_and_pauses(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    statistic_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backfill requests 30-day chunks, pauses between them and finishes."""
    coordinator = entry.runtime_data.coordinator
    pauses: list[float] = []

    async def fake_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        pauses.append(delay)

    monkeypatch.setattr(statistics_module.asyncio, "sleep", fake_sleep)

    today = FROZEN_NOW.date()
    with freeze_time(FROZEN_NOW):
        await async_backfill(hass, entry, coordinator, days=BACKFILL_CHUNK_DAYS * 3)

    end = today - timedelta(days=WINDOW_END_DAYS_BACK)
    start = end - timedelta(days=BACKFILL_CHUNK_DAYS * 3 - 1)
    assert coordinator.client.calls == [
        (start, start + timedelta(days=BACKFILL_CHUNK_DAYS - 1), DAYS_AGGREGATION),
        (
            start + timedelta(days=BACKFILL_CHUNK_DAYS),
            start + timedelta(days=BACKFILL_CHUNK_DAYS * 2 - 1),
            DAYS_AGGREGATION,
        ),
        (
            start + timedelta(days=BACKFILL_CHUNK_DAYS * 2),
            end,
            DAYS_AGGREGATION,
        ),
    ]
    # One pause between three chunks: the loop does not sleep after the last one.
    assert pauses == [BACKFILL_PAUSE_SECONDS, BACKFILL_PAUSE_SECONDS]
    assert coordinator.paced == 3

    state = entry.runtime_data.statistics_state
    assert state.done is True
    assert state.running is False
    assert state.chunks_done == state.chunks_total == 3
    assert state.next_start is None

    rows = await read_rows(hass, statistic_id)
    assert len(rows) == BACKFILL_CHUNK_DAYS * 3
    assert rows[-1]["sum"] == pytest.approx(3.0 * BACKFILL_CHUNK_DAYS * 3)


async def test_backfill_resumes_from_the_stored_offset(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    statistic_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored chunk offset is resumed, not restarted from the window start."""
    coordinator = entry.runtime_data.coordinator
    monkeypatch.setattr(
        statistics_module.asyncio, "sleep", AsyncMock(spec=statistics_module.asyncio.sleep)
    )

    today = FROZEN_NOW.date()
    end = today - timedelta(days=WINDOW_END_DAYS_BACK)
    start = end - timedelta(days=BACKFILL_CHUNK_DAYS * 3 - 1)
    resumed_at = start + timedelta(days=BACKFILL_CHUNK_DAYS)

    store = statistics_module._statistics_store(hass, entry)
    await store.async_load()
    store.set_backfill(
        BackfillProgress(
            days=BACKFILL_CHUNK_DAYS * 3,
            window_start=start,
            next_start=resumed_at,
            chunks_done=1,
            done=False,
        )
    )
    await store.async_save()

    with freeze_time(FROZEN_NOW):
        await statistics_module.async_prepare_statistics(hass, entry)
        state = entry.runtime_data.statistics_state
        assert state.done is False
        assert state.next_start == resumed_at

        await async_backfill(hass, entry, coordinator, days=BACKFILL_CHUNK_DAYS * 3)

    assert coordinator.client.calls[0][0] == resumed_at
    assert len(coordinator.client.calls) == 2
    assert state.done is True
    assert state.chunks_done == 3
    assert store.backfill is not None
    assert store.backfill.done is True


async def test_backfill_stops_on_a_revoked_sign_in(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    statistic_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``CkwAuthError`` stops the job, keeps the offset and starts reauth."""
    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = CkwAuthError("token expired")
    reauth = Mock()
    monkeypatch.setattr("homeassistant.config_entries.ConfigEntry.async_start_reauth", reauth)

    try:
        with freeze_time(FROZEN_NOW):
            await async_backfill(hass, entry, coordinator, days=BACKFILL_CHUNK_DAYS * 2)
    finally:
        coordinator.client.error = None

    assert reauth.call_count == 1
    assert len(coordinator.client.calls) == 1
    state = entry.runtime_data.statistics_state
    assert state.done is False
    assert state.running is False
    store = statistics_module._statistics_store(hass, entry)
    assert store.backfill is None


# ---------------------------------------------------------------------------
# The `ckw.backfill` service
# ---------------------------------------------------------------------------


async def _loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return the entry marked as loaded, as a running setup leaves it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
        title="CKW service test",
        unique_id="statistics-service",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        api=None,
        coordinator=FakeCoordinator(),
        statistics_state=StatisticsState(),
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


async def test_backfill_service_calls_the_backfill_with_the_requested_days(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service re-runs the backfill for the entry it is given."""
    entry = await _loaded_entry(hass)
    backfill = AsyncMock()
    monkeypatch.setattr(statistics_module, "async_backfill", backfill)
    async_register_backfill_service(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_BACKFILL,
        {ATTR_ENTRY_ID: entry.entry_id, ATTR_DAYS: 45, ATTR_FORCE: True},
        blocking=True,
    )

    backfill.assert_awaited_once()
    args, kwargs = backfill.await_args
    assert args[1] is entry
    assert args[2] is entry.runtime_data.coordinator
    assert args[3] == 45
    assert kwargs == {"force": True, "raise_on_error": True}


async def test_backfill_service_validates_its_fields(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-of-range day count and an unknown entry id are refused."""
    entry = await _loaded_entry(hass)
    backfill = AsyncMock()
    monkeypatch.setattr(statistics_module, "async_backfill", backfill)
    async_register_backfill_service(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, SERVICE_BACKFILL, {ATTR_DAYS: 0}, blocking=True)

    with pytest.raises(Exception) as error:
        await hass.services.async_call(
            DOMAIN, SERVICE_BACKFILL, {ATTR_ENTRY_ID: "nope"}, blocking=True
        )
    assert error.value.translation_key == "unknown_entry"  # type: ignore[attr-defined]
    backfill.assert_not_awaited()
    assert entry.entry_id


# ---------------------------------------------------------------------------
# The setup path: the backfill runs once per entry, not once per restart
# ---------------------------------------------------------------------------

CUSTOMER_ID: Final = "0000000099"


class FakeApi:
    """Replay daily intervals for any window; no socket is opened."""

    def __init__(self) -> None:
        self.calls: list[tuple[date, date, str]] = []

    async def async_get_measurements(
        self,
        start: date,
        end: date,
        aggregation: str = DAYS_AGGREGATION,
    ) -> list[CkwMeterMeasurements]:
        self.calls.append((start, end, aggregation))
        return [
            CkwMeterMeasurements(
                deliverypointnumber=METER,
                measurements=tuple(_daily(day) for day in _days(start, (end - start).days + 1)),
            )
        ]

    async def async_get_customer(self) -> CkwCustomer:
        return CkwCustomer(id=CUSTOMER_ID, locations=())


class FakeOAuth2Implementation(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    """Local OAuth2 implementation that hands out one fixed token pair."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    @property
    def name(self) -> str:
        return "CKW test IdP"

    @property
    def domain(self) -> str:
        return DOMAIN

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        return "https://id.example.invalid/authorize"

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        return {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    async def _async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        return {
            **token,
            "access_token": "refreshed-access-token",
            "refresh_token": "refreshed-refresh-token",
            "expires_in": 3600,
        }


async def test_setup_runs_the_backfill_once(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first setup imports the whole history; a restart does not repeat it."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, FakeOAuth2Implementation(hass)
    )
    api = FakeApi()
    monkeypatch.setattr(CkwClient, "async_get_measurements", api.async_get_measurements)
    monkeypatch.setattr(CkwClient, "async_get_customer", api.async_get_customer)
    monkeypatch.setattr(
        statistics_module.asyncio, "sleep", AsyncMock(spec=statistics_module.asyncio.sleep)
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CUSTOMER_ID: CUSTOMER_ID,
            CONF_CITY: "Luzern",
            CONF_METER: METER,
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_in": 3600,
                "expires_at": datetime.now(UTC).timestamp() + 3600,
                "token_type": "Bearer",
            },
        },
        options={},
        unique_id=CUSTOMER_ID,
        title=f"{CUSTOMER_ID} - Luzern",
    )
    entry.add_to_hass(hass)
    today = FROZEN_NOW.date()

    with freeze_time(FROZEN_NOW):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

        state = entry.runtime_data.statistics_state
        assert state.done is True
        chunks = math.ceil(BACKFILL_DAYS / BACKFILL_CHUNK_DAYS)
        # The backfill's chunks, then the poll's rolling window. The poll also
        # asks for today's quarter hours, which no statistic is made from.
        day_calls = [call for call in api.calls if call[2] == DAYS_AGGREGATION]
        assert len(day_calls) == chunks + 1
        # The first day call is the poll: the rolling window, ending yesterday.
        assert day_calls[0] == (
            today - timedelta(days=ROLLING_WINDOW_DAYS),
            today - timedelta(days=WINDOW_END_DAYS_BACK),
            DAYS_AGGREGATION,
        )
        assert hass.services.has_service(DOMAIN, SERVICE_BACKFILL)

        # The statistic id is the entity id of the total sensor (ADR-0002).
        statistic = er.async_get(hass).async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{entry.entry_id}_{METER}_{STATISTIC_ENTITY_KEY}"
        )
        assert statistic is not None
        rows = await read_rows(hass, statistic)
        assert len(rows) == BACKFILL_DAYS
        assert rows[-1]["sum"] == pytest.approx(3.0 * BACKFILL_DAYS)

        # A restart finds the stored state and imports nothing again.
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        calls_before = len(day_calls)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data.statistics_state.done is True
    day_calls = [call for call in api.calls if call[2] == DAYS_AGGREGATION]
    assert len(day_calls) == calls_before + 1  # the poll only, no chunk again


# ---------------------------------------------------------------------------
# Resume, abort and malformed-state paths (Silver test-coverage)
# ---------------------------------------------------------------------------


async def test_a_second_backfill_is_refused_while_one_is_running(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Two runs would race on one stored offset, so the second returns at once."""
    coordinator = entry.runtime_data.coordinator
    entry.runtime_data.statistics_state.running = True

    await async_backfill(hass, entry, coordinator, days=1)

    assert coordinator.client.calls == []


async def test_a_forced_backfill_clears_the_stored_anchors_first(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    statistic_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild from the first day must not continue an untrusted sum."""
    coordinator = entry.runtime_data.coordinator
    store = statistics_module._statistics_store(hass, entry)
    await store.async_load()
    store.set_meter_state(
        METER,
        MeterState(
            anchor_start=datetime(2025, 5, 1, tzinfo=UTC),
            anchor_sum=42.0,
            checksum="old",
        ),
    )
    monkeypatch.setattr(
        statistics_module,
        "async_import_meter_statistics",
        AsyncMock(return_value=SimpleNamespace(rows=0)),
    )
    monkeypatch.setattr(
        statistics_module.asyncio, "sleep", AsyncMock(spec=statistics_module.asyncio.sleep)
    )

    with freeze_time(FROZEN_NOW):
        await async_backfill(hass, entry, coordinator, days=1, force=True)

    assert store.meter_state(METER) is None


async def test_polled_import_without_data_or_with_only_gas_does_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A failed poll has no data, and a gas meter has no energy statistic."""
    coordinator = entry.runtime_data.coordinator

    coordinator.data = None
    await async_import_polled_statistics(hass, entry)

    coordinator.data = SimpleNamespace(
        meters={METER: object()},
        meter_info={METER: SimpleNamespace(fuel=Fuel.GAS)},
    )
    await async_import_polled_statistics(hass, entry)

    assert coordinator.client.calls == []


async def test_polled_import_skips_a_meter_without_intervals(
    hass: HomeAssistant, entry: MockConfigEntry, statistic_id: str
) -> None:
    """A known meter with no interval has nothing to write."""
    coordinator = entry.runtime_data.coordinator
    coordinator.data = SimpleNamespace(meters={METER: object()}, meter_info={})

    await async_import_polled_statistics(hass, entry)

    assert coordinator.client.calls == []


async def test_a_raw_http_failure_in_a_chunk_keeps_the_offset(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A non-auth response error is logged and the next run resumes."""
    from unittest.mock import Mock

    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = aiohttp.ClientResponseError(Mock(), (), status=503)

    result = await _async_import_chunk(hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1))

    assert result is None


async def test_a_timeout_in_a_chunk_is_a_connection_failure_for_the_service(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The service gets a translated error instead of an aiohttp timeout."""
    from custom_components.ckw.exceptions import CkwConnectionFailedError

    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = TimeoutError("slow")

    with pytest.raises(CkwConnectionFailedError):
        await _async_import_chunk(
            hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1), raise_on_error=True
        )


async def test_a_generic_api_failure_in_a_chunk_uses_the_shared_mapping(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A failure without a more specific key becomes ``unknown`` for the service."""
    from custom_components.ckw.exceptions import CkwUnknownError

    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = CkwError("something new")

    with pytest.raises(CkwUnknownError):
        await _async_import_chunk(
            hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1), raise_on_error=True
        )


async def test_a_chunk_without_a_sensor_is_retried_instead_of_skipped(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Importing nothing would advance the offset past data never written."""
    from custom_components.ckw.exceptions import CkwNoDataError

    coordinator = entry.runtime_data.coordinator
    coordinator.client.series = [
        CkwMeterMeasurements(
            deliverypointnumber=METER,
            measurements=tuple(_daily(day) for day in _days(date(2025, 5, 1), 2)),
        )
    ]

    assert (
        await _async_import_chunk(hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 2))
        is None
    )

    with pytest.raises(CkwNoDataError):
        await _async_import_chunk(
            hass,
            entry,
            coordinator,
            date(2025, 5, 1),
            date(2025, 5, 2),
            raise_on_error=True,
        )


async def test_the_service_refuses_when_no_entry_is_loaded(hass: HomeAssistant) -> None:
    """Before any setup there is no coordinator the service could use."""
    with pytest.raises(Exception) as raised:
        _backfill_targets(hass, None)

    assert raised.value.translation_key == "no_loaded_entry"  # type: ignore[attr-defined]


def test_a_bucket_without_a_timestamp_or_on_an_unaligned_hour_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeless row and a row the recorder would reject never become a bucket."""
    timeless = CkwMeasurement(
        start_utc=None,
        end_utc=None,
        kwh_ht=1.0,
        kwh_nt=1.0,
        chf_ht=None,
        chf_nt=None,
        lines_p=1,
        lines_fw=1,
        lines_fb=1,
    )
    aligned = _daily(date(2025, 5, 1))
    monkeypatch.setattr(
        statistics_module,
        "_local_midnight",
        lambda day: datetime.combine(day, time(0, 30), tzinfo=TIME_ZONE),
    )

    buckets, skipped = _daily_buckets([timeless, aligned])

    assert buckets == []
    assert skipped == 2


def test_an_older_window_restarts_the_cumulative_sum() -> None:
    """History older than the anchor cannot continue a sum that starts later."""
    previous = MeterState(
        anchor_start=datetime(2025, 5, 10, tzinfo=TIME_ZONE),
        anchor_sum=30.0,
        checksum="old",
    )
    buckets = [
        StatisticsBucket(start=datetime(2025, 5, 1, tzinfo=TIME_ZONE), kwh=2.0),
        StatisticsBucket(start=datetime(2025, 5, 2, tzinfo=TIME_ZONE), kwh=3.0),
    ]

    rows, state = _cumulative_rows(buckets, previous)

    assert [row["sum"] for row in rows] == [2.0, 5.0]
    assert state.anchor_sum == 5.0


def test_local_day_prefers_the_start_and_can_use_the_end() -> None:
    """A row with only an end belongs to the day that end closes."""
    end_only = CkwMeasurement(
        start_utc=None,
        end_utc=datetime(2025, 5, 2, tzinfo=TIME_ZONE).astimezone(UTC),
        kwh_ht=1.0,
        kwh_nt=1.0,
        chf_ht=None,
        chf_nt=None,
        lines_p=1,
        lines_fw=1,
        lines_fb=1,
    )
    nothing = CkwMeasurement(
        start_utc=None,
        end_utc=None,
        kwh_ht=1.0,
        kwh_nt=1.0,
        chf_ht=None,
        chf_nt=None,
        lines_p=1,
        lines_fw=1,
        lines_fb=1,
    )

    assert _local_day(end_only) == date(2025, 5, 1)
    assert _local_day(nothing) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not-a-mapping",
        {"anchor_start": 1, "anchor_sum": 1.0, "checksum": "x"},
        {"anchor_start": "2025-05-01T00:00:00", "anchor_sum": True, "checksum": "x"},
        {"anchor_start": "2025-05-01T00:00:00", "anchor_sum": 1.0, "checksum": 2},
        {"anchor_start": "not-a-date", "anchor_sum": 1.0, "checksum": "x"},
    ],
)
def test_a_malformed_meter_anchor_is_ignored(raw: object) -> None:
    """An older or hand-edited store must not break the import."""
    assert _parse_meter_state(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"days": True},
        {"days": 1, "chunks_done": "1"},
        {"days": 1, "chunks_done": 0, "window_start": 1, "next_start": "2025-05-01"},
        {
            "days": 1,
            "chunks_done": 0,
            "window_start": "2025-05-01",
            "next_start": "2025-05-02",
            "done": "no",
        },
        {
            "days": 1,
            "chunks_done": 0,
            "window_start": "not-a-date",
            "next_start": "2025-05-02",
            "done": False,
        },
    ],
)
def test_a_malformed_backfill_offset_is_ignored(raw: object) -> None:
    """A corrupt offset means start over, never a crash during setup."""
    assert _parse_backfill(raw) is None


async def test_polled_import_logs_a_meter_without_a_total_sensor(
    hass: HomeAssistant, entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    """Before the entity exists there is no statistic id to write to."""
    coordinator = entry.runtime_data.coordinator
    coordinator.data = SimpleNamespace(meters={METER: object()}, meter_info={})
    coordinator.interval_data = (_daily(date(2025, 5, 1)),)
    caplog.set_level("DEBUG", logger="custom_components.ckw.statistics")

    await async_import_polled_statistics(hass, entry)

    assert any("no total sensor" in record.getMessage() for record in caplog.records)


async def test_a_raw_http_failure_is_raised_when_the_service_asked(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The service sees its own connection error, not the aiohttp one."""
    from unittest.mock import Mock

    from custom_components.ckw.exceptions import CkwConnectionFailedError

    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = aiohttp.ClientResponseError(Mock(), (), status=503)

    with pytest.raises(CkwConnectionFailedError):
        await _async_import_chunk(
            hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1), raise_on_error=True
        )


async def test_a_timeout_keeps_the_offset_for_the_background_run(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Nobody asked for an error, so a timeout resumes at the same chunk."""
    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = TimeoutError("slow")

    assert (
        await _async_import_chunk(hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1))
        is None
    )


async def test_a_generic_api_failure_keeps_the_offset_for_the_background_run(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A background chunk failure is a log line and a stored offset, not a crash."""
    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = CkwError("something new")

    assert (
        await _async_import_chunk(hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1))
        is None
    )


async def test_a_raw_auth_failure_also_stops_the_background_run(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead token refresh starts reauth and returns without raising."""
    from unittest.mock import Mock

    coordinator = entry.runtime_data.coordinator
    coordinator.client.error = aiohttp.ClientResponseError(Mock(), (), status=401)
    monkeypatch.setattr(entry, "async_start_reauth", Mock())

    assert (
        await _async_import_chunk(hass, entry, coordinator, date(2025, 5, 1), date(2025, 5, 1))
        is None
    )
    entry.async_start_reauth.assert_called_once()  # type: ignore[attr-defined]


async def test_the_poll_listener_is_silent_while_a_backfill_runs(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backfill imports the same days; the listener must not double-write them."""
    coordinator = entry.runtime_data.coordinator
    tasks: list[str] = []

    def create_task(_hass: HomeAssistant, coroutine: Any, *, name: str) -> None:
        coroutine.close()
        tasks.append(name)

    monkeypatch.setattr(entry, "async_create_background_task", create_task)
    statistics_module.async_register_statistics(hass, entry)

    entry.runtime_data.statistics_state.running = True
    coordinator.listener()
    assert tasks == []

    entry.runtime_data.statistics_state.running = False
    coordinator.listener()
    assert tasks == ["ckw statistics import"]


def test_a_window_that_starts_later_than_the_anchor_continues_the_sum() -> None:
    """A gap between the anchor and this window carries the sum forward."""
    previous = MeterState(
        anchor_start=datetime(2025, 5, 1, tzinfo=TIME_ZONE),
        anchor_sum=3.0,
        checksum="old",
    )
    buckets = [StatisticsBucket(start=datetime(2025, 5, 5, tzinfo=TIME_ZONE), kwh=2.0)]

    rows, state = _cumulative_rows(buckets, previous)

    assert [row["sum"] for row in rows] == [5.0]
    assert state.anchor_sum == 5.0


def test_has_electricity_series_requires_a_named_electricity_meter() -> None:
    """An empty or gas-only answer is not an electricity history."""
    from custom_components.ckw.statistics import _has_electricity_series

    coordinator = SimpleNamespace(data=SimpleNamespace(meter_info={}))
    empty = [CkwMeterMeasurements(deliverypointnumber="", measurements=())]

    assert _has_electricity_series(coordinator, empty) is False
    assert _has_electricity_series(
        coordinator, [CkwMeterMeasurements(deliverypointnumber=METER, measurements=())]
    )


@pytest.mark.parametrize("raw_date", ["not-a-date", "2025-13-01T00:00:00"])
def test_an_unparseable_anchor_date_is_ignored(raw_date: str) -> None:
    """Both a missing and an impossible date mean no stored anchor."""
    assert (
        _parse_meter_state({"anchor_start": raw_date, "anchor_sum": 1.0, "checksum": "x"}) is None
    )
