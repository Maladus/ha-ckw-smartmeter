"""Tests for the coordinator: rolling window, totals, pacing and logging.

The network is never touched: ``CkwClient.async_get_measurements`` is replaced by
a callable that replays the committed fixtures under ``tests/fixtures/``, so the
coordinator, the config entry setup and the OAuth2 session are the real objects.
Time is frozen with ``freezegun`` because the window, the
``publication_delay_h`` value and the lag warnings are all relative to "now".

The platform forward is patched out: ``PLATFORMS`` names ``sensor`` and the
entity platform lands in its own task, so forwarding it here would load a module
that does not exist yet.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw import coordinator as coordinator_module
from custom_components.ckw.api import (
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwMeasurement,
    CkwMeterMeasurements,
    CkwRangeTooBigError,
    CkwRateLimitError,
    RateLimitInfo,
    mask_deliverypoint,
)
from custom_components.ckw.const import (
    BACKFILL_DAYS,
    CONF_BACKFILL_ENABLED,
    CONF_CITY,
    CONF_CUSTOMER_ID,
    CONF_METER,
    CONF_POLL_HOURS,
    DAYS_AGGREGATION,
    DEFAULT_POLL_HOURS,
    DOMAIN,
    MANUFACTURER,
    SERVICE_MODEL,
    TIME_ZONE,
)
from custom_components.ckw.coordinator import CkwCoordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIXTURE_DIR: Final[Path] = Path(__file__).parent / "fixtures"
COORDINATOR_LOGGER: Final = "custom_components.ckw.coordinator"
FIXTURE_NAME: Final = "measurements_days.json"

CUSTOMER_ID: Final = "0000000099"
CITY: Final = "Luzern"
ENTRY_METER: Final = "CH1003601234500000000000000000000"

#: Frozen poll time, winter (CET, UTC+1): 2026-11-02 11:00 UTC.
FROZEN_NOW: Final = datetime(2026, 11, 2, 12, 0, tzinfo=TIME_ZONE)


# ---------------------------------------------------------------------------
# Fixtures as the API returns them
# ---------------------------------------------------------------------------


def _utc(value: str) -> datetime:
    """Parse a fixture timestamp, which is a naive UTC ``dateTimeOnly``."""
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _measurement(entry: dict[str, Any]) -> CkwMeasurement:
    """Build the parsed measurement of one fixture entry."""
    return CkwMeasurement(
        start_utc=_utc(entry["zeitstempel_von_utc"]),
        end_utc=_utc(entry["zeitstempel_bis_utc"]),
        kwh_ht=entry["menge_fakturiert_ht"],
        kwh_nt=entry["menge_fakturiert_nt"],
        chf_ht=entry["betrag_ht"],
        chf_nt=entry["betrag_nt"],
        lines_p=entry["anzahl_linien_p"],
        lines_fw=entry["anzahl_linien_fw"],
        lines_fb=entry["anzahl_linien_fb"],
    )


def _next_day(last: CkwMeasurement) -> CkwMeasurement:
    """Return the day after ``last``, as CKW publishes it one day at a time."""
    assert last.end_utc is not None
    return CkwMeasurement(
        start_utc=last.end_utc,
        end_utc=last.end_utc + timedelta(days=1),
        kwh_ht=2.0,
        kwh_nt=1.0,
        chf_ht=0.6,
        chf_nt=0.3,
        lines_p=1,
        lines_fw=1,
        lines_fb=1,
    )


def load_series(*, extra_day: bool = False) -> list[CkwMeterMeasurements]:
    """Replay the committed daily fixture, optionally one day longer."""
    document = json.loads((FIXTURE_DIR / FIXTURE_NAME).read_text(encoding="utf-8"))
    series: list[CkwMeterMeasurements] = []
    for deliverypoint, entries in document["series"].items():
        measurements = [_measurement(entry) for entry in entries]
        if extra_day:
            measurements.append(_next_day(measurements[-1]))
        series.append(
            CkwMeterMeasurements(
                deliverypointnumber=deliverypoint,
                measurements=tuple(measurements),
            )
        )
    return series


class FakeMeasurements:
    """Stands in for ``CkwClient.async_get_measurements``; no socket is opened."""

    def __init__(self, series: list[CkwMeterMeasurements] | None = None) -> None:
        self.series = series or []
        self.error: Exception | None = None
        self.calls: list[tuple[date, date, str]] = []
        self.events: list[str] = []

    async def __call__(
        self, start: date, end: date, aggregation: str = DAYS_AGGREGATION
    ) -> list[CkwMeterMeasurements]:
        self.calls.append((start, end, aggregation))
        self.events.append("request")
        if self.error is not None:
            raise self.error
        return self.series


class RangeTooBigOnce(FakeMeasurements):
    """Rejects the first window with the documented ``400``, then answers."""

    async def __call__(
        self, start: date, end: date, aggregation: str = DAYS_AGGREGATION
    ) -> list[CkwMeterMeasurements]:
        self.calls.append((start, end, aggregation))
        self.events.append("request")
        if len(self.calls) == 1:
            raise CkwRangeTooBigError(
                400, "response too big, please adjust aggregation or duration accordingly"
            )
        return self.series


# ---------------------------------------------------------------------------
# Config entry and OAuth2 helpers
# ---------------------------------------------------------------------------


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


def register_implementation(hass: HomeAssistant) -> None:
    """Register the fake implementation as the only OAuth2 implementation."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, FakeOAuth2Implementation(hass)
    )


def add_entry(
    hass: HomeAssistant,
    *,
    options: dict[str, Any] | None = None,
    state: ConfigEntryState | None = None,
) -> MockConfigEntry:
    """Add a configured CKW entry with a still-valid token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CUSTOMER_ID: CUSTOMER_ID,
            CONF_CITY: CITY,
            CONF_METER: ENTRY_METER,
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "token_type": "Bearer",
            },
        },
        options=options or {},
        state=state,
        unique_id=CUSTOMER_ID,
        title=f"{CUSTOMER_ID} — {CITY}",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def platform_calls(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Keep platform setup out of the way; ``sensor.py`` lands in its own task."""
    forwarded: list[tuple[str, ...]] = []

    async def forward(entry: ConfigEntry, platforms: Iterable[Platform | str]) -> None:
        forwarded.append(tuple(str(platform) for platform in platforms))

    async def unload(entry: ConfigEntry, platforms: Iterable[Platform | str]) -> bool:
        return True

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forward)
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload)
    return forwarded


def make_client() -> CkwClient:
    """Build a client whose token provider always returns a test token."""

    async def token_provider() -> str:
        return "test-access-token"

    return CkwClient(AsyncMock(), token_provider)


def messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """Return the coordinator's log lines of one level."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == COORDINATOR_LOGGER and record.levelno == level
    ]


async def setup_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeMeasurements,
    *,
    backfill: bool | None = False,
) -> ConfigEntry:
    """Set up a real config entry whose client replays ``fake``.

    The background backfill is off by default: these tests assert the
    coordinator's poll requests, and an enabled backfill would add its own
    chunks to the same call list. ``backfill=None`` leaves the integration's
    default untouched, which is what the scheduling test needs.
    """
    register_implementation(hass)
    monkeypatch.setattr(CkwClient, "async_get_measurements", fake)
    options = {} if backfill is None else {CONF_BACKFILL_ENABLED: backfill}
    entry = add_entry(hass, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# Rolling window, totals and publication logging
# ---------------------------------------------------------------------------


async def test_two_polls_grow_the_totals_and_log_one_publication(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """A second poll with one more day grows the totals and logs one INFO line."""
    fake = FakeMeasurements(load_series())

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        coordinator = entry.runtime_data.coordinator
        first = coordinator.data.meters[ENTRY_METER]

        # The window ends yesterday in Europe/Zurich: 2026-11-01, seven days.
        assert fake.calls == [(date(2026, 10, 26), date(2026, 11, 1), DAYS_AGGREGATION)]
        # The newest fixture interval ends 2026-10-31T23:00Z, 36 h before now.
        assert first.publication_delay_h == pytest.approx(36.0)

        caplog.clear()
        fake.series = load_series(extra_day=True)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        second = coordinator.data.meters[ENTRY_METER]

    assert second.kwh_total > first.kwh_total
    assert second.kwh_ht > first.kwh_ht
    assert second.kwh_nt > first.kwh_nt
    assert second.intervals_seen == first.intervals_seen + 1
    assert coordinator.new_intervals[ENTRY_METER] == 1
    # The added local day ends 2026-11-01T23:00Z, 12 h before the frozen now.
    assert second.publication_delay_h == pytest.approx(12.0)

    info = messages(caplog, logging.INFO)
    assert len(info) == 1
    assert info[0].startswith("new grid data published: 2026-11-01, lag 12.0 h")
    assert mask_deliverypoint(ENTRY_METER) == "*" * 29 + "0000"
    assert mask_deliverypoint(ENTRY_METER) in info[0]


async def test_the_default_options_schedule_the_history_backfill(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """Without an explicit option setup starts the 730-day history import."""
    fake = FakeMeasurements(load_series())
    backfill = AsyncMock()
    monkeypatch.setattr("custom_components.ckw.async_backfill", backfill)

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake, backfill=None)
        await hass.async_block_till_done(wait_background_tasks=True)

    backfill.assert_awaited_once()
    args, _ = backfill.await_args
    assert args[0] is hass
    assert args[1] is entry
    assert args[3] == BACKFILL_DAYS
    # The poll before it is still the plain rolling window.
    assert fake.calls == [(date(2026, 10, 26), date(2026, 11, 1), DAYS_AGGREGATION)]


async def test_an_unchanged_poll_is_quiet_but_logs_at_debug(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """The same window twice changes nothing, so no INFO line is written."""
    fake = FakeMeasurements(load_series())

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        coordinator = entry.runtime_data.coordinator
        first = coordinator.data.meters[ENTRY_METER]

        caplog.clear()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        second = coordinator.data.meters[ENTRY_METER]

    assert second == first
    assert coordinator.new_intervals[ENTRY_METER] == 0
    assert messages(caplog, logging.INFO) == []
    assert any("changed: no" in line for line in messages(caplog, logging.DEBUG))


async def test_a_duplicate_interval_is_not_counted_twice(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """Overlapping windows overlap: only the unseen interval grows the totals."""
    fake = FakeMeasurements(load_series(extra_day=True))

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        coordinator = entry.runtime_data.coordinator
        first = coordinator.data.meters[ENTRY_METER]

        await coordinator.async_refresh()
        await hass.async_block_till_done()
        second = coordinator.data.meters[ENTRY_METER]

    assert second == first
    assert coordinator.new_intervals[ENTRY_METER] == 0


async def test_a_long_lag_warns_once(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """A lag above the documented threshold warns once, not on every poll."""
    fake = FakeMeasurements(load_series())

    with freeze_time(datetime(2026, 11, 10, 12, 0, tzinfo=TIME_ZONE)):
        entry = await setup_entry(hass, monkeypatch, fake)
        coordinator = entry.runtime_data.coordinator
        warnings = messages(caplog, logging.WARNING)
        assert any("more than the expected 96 h" in line for line in warnings)

        caplog.clear()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert messages(caplog, logging.WARNING) == []


async def test_an_interval_that_never_advances_warns_after_four_days(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """A feed that stopped publishing is called out, not silently swallowed."""
    fake = FakeMeasurements(load_series())

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        coordinator = entry.runtime_data.coordinator

    caplog.clear()
    with freeze_time(FROZEN_NOW + timedelta(days=5)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    warnings = messages(caplog, logging.WARNING)
    assert any("has not advanced for 4 days" in line for line in warnings)


# ---------------------------------------------------------------------------
# Exception mapping (#70)
# ---------------------------------------------------------------------------


async def test_an_auth_failure_asks_for_reauth(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """A rejected sign-in becomes ``ConfigEntryAuthFailed`` and starts reauth."""
    fake = FakeMeasurements(load_series())
    entry = await setup_entry(hass, monkeypatch, fake)
    coordinator = entry.runtime_data.coordinator
    fake.error = CkwAuthError("Bad or expired token")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    error = coordinator.last_exception
    assert isinstance(error, ConfigEntryAuthFailed)
    assert "reauthentication is required" in str(error)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"].get("source") == SOURCE_REAUTH for flow in flows)


async def test_a_rate_limit_failure_names_the_reset_time(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """A ``429`` fails the poll with the reset time, and is not retried in a loop."""
    fake = FakeMeasurements(load_series())
    entry = await setup_entry(hass, monkeypatch, fake)
    coordinator = entry.runtime_data.coordinator
    fake.error = CkwRateLimitError(
        "Quota has been exceeded", retry_after_seconds=120.0, remaining=0
    )

    await coordinator.async_refresh()

    error = coordinator.last_exception
    assert isinstance(error, UpdateFailed)
    assert "resets in 120 s" in str(error)
    assert error.retry_after == pytest.approx(120.0)


async def test_a_range_too_big_answer_halves_the_window_once(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """``400`` "response too big" retries once with half the window, then succeeds."""
    fake = RangeTooBigOnce(load_series())

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)

    assert [call[:2] for call in fake.calls] == [
        (date(2026, 10, 26), date(2026, 11, 1)),
        (date(2026, 10, 30), date(2026, 11, 1)),
    ]
    assert entry.runtime_data.coordinator.last_update_success
    assert entry.runtime_data.coordinator.data.meters
    assert any("too big" in line for line in messages(caplog, logging.WARNING))


# ---------------------------------------------------------------------------
# Rate-limit pacing (#147)
# ---------------------------------------------------------------------------


async def test_an_exhausted_quota_is_awaited_before_the_request(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """``remaining=0`` sleeps for ``x-ratelimit-reset`` and still sends the request."""
    fake = FakeMeasurements(load_series())
    entry = await setup_entry(hass, monkeypatch, fake)
    coordinator = entry.runtime_data.coordinator

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake.events.append("sleep")

    monkeypatch.setattr(coordinator_module, "asyncio", SimpleNamespace(sleep=fake_sleep))
    fake.events.clear()
    coordinator.client.last_rate_limit = RateLimitInfo(limit=4, remaining=0, reset_ms=2000)

    await coordinator.async_refresh()

    assert sleeps == [2.0]
    assert fake.events == ["sleep", "request"]


# ---------------------------------------------------------------------------
# Setup, unload and the poll interval
# ---------------------------------------------------------------------------


async def test_a_failed_first_refresh_is_not_ready(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """A broken first poll raises ``ConfigEntryNotReady``, never a half entry."""
    register_implementation(hass)
    fake = FakeMeasurements()
    fake.error = CkwConnectionError("offline")
    monkeypatch.setattr(CkwClient, "async_get_measurements", fake)
    entry = add_entry(hass, state=ConfigEntryState.SETUP_IN_PROGRESS)
    coordinator = CkwCoordinator(hass, entry, make_client())

    with pytest.raises(ConfigEntryNotReady):
        await coordinator.async_config_entry_first_refresh()


async def test_setup_registers_the_service_device_and_unload_clears_the_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """Setup builds runtime data and the service device; unload removes both."""
    entry = await setup_entry(hass, monkeypatch, FakeMeasurements(load_series()))

    assert entry.state is ConfigEntryState.LOADED
    runtime = entry.runtime_data
    assert isinstance(runtime.coordinator, CkwCoordinator)
    assert runtime.coordinator.client is runtime.api
    assert platform_calls == [("sensor",)]

    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.entry_type is DeviceEntryType.SERVICE
    assert (device.manufacturer, device.model) == (MANUFACTURER, SERVICE_MODEL)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(entry, "runtime_data")


async def test_the_poll_interval_comes_from_the_options(hass: HomeAssistant) -> None:
    """The options flow's interval sets the refresh timer, bounds included."""
    entry = add_entry(hass, options={CONF_POLL_HOURS: 4})
    coordinator = CkwCoordinator(hass, entry, make_client())
    assert coordinator.update_interval == timedelta(hours=4)

    out_of_bounds = add_entry(hass, options={CONF_POLL_HOURS: 99})
    fallback = CkwCoordinator(hass, out_of_bounds, make_client())
    assert fallback.update_interval == timedelta(hours=DEFAULT_POLL_HOURS)


# ---------------------------------------------------------------------------
# Timezone discipline (#73)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frozen", "expected"),
    [
        # DST starts 2026-03-29 02:00 local: at 00:30 the UTC date is still the 28th.
        (
            datetime(2026, 3, 29, 0, 30, tzinfo=TIME_ZONE),
            (date(2026, 3, 22), date(2026, 3, 28)),
        ),
        # DST ends 2026-10-25 03:00 local: at 00:30 the UTC date is still the 24th.
        (
            datetime(2026, 10, 25, 0, 30, tzinfo=TIME_ZONE),
            (date(2026, 10, 18), date(2026, 10, 24)),
        ),
    ],
)
async def test_the_window_uses_local_dates_on_dst_days(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
    frozen: datetime,
    expected: tuple[date, date],
) -> None:
    """The ``YYYYMMDD`` path parameters follow the local day, not the UTC one."""
    fake = FakeMeasurements(load_series())

    with freeze_time(frozen):
        await setup_entry(hass, monkeypatch, fake)

    assert fake.calls[0][:2] == expected
    assert fake.calls[0][2] == DAYS_AGGREGATION


# ---------------------------------------------------------------------------
# log-when-unavailable (#72)
# ---------------------------------------------------------------------------


async def test_a_failure_warns_once_and_the_recovery_is_info(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """First failure warns, repeats stay debug, and the recovery is logged once."""
    fake = FakeMeasurements(load_series())
    entry = await setup_entry(hass, monkeypatch, fake)
    coordinator = entry.runtime_data.coordinator

    caplog.clear()
    fake.error = CkwConnectionError("offline")
    await coordinator.async_refresh()
    warnings = messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "failed, retrying at the next interval" in warnings[0]
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert not coordinator.last_update_success

    caplog.clear()
    await coordinator.async_refresh()
    assert messages(caplog, logging.WARNING) == []
    assert any("failed:" in line for line in messages(caplog, logging.DEBUG))

    caplog.clear()
    fake.error = None
    await coordinator.async_refresh()
    assert any("recovered" in line for line in messages(caplog, logging.INFO))
    assert coordinator.last_update_success


async def test_a_window_that_is_still_too_big_fails_the_poll(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platform_calls: list[tuple[str, ...]],
) -> None:
    """Halving happens once; a second ``400`` is an ``UpdateFailed``, not a loop."""
    register_implementation(hass)
    fake = FakeMeasurements(load_series())
    fake.error = CkwRangeTooBigError(400, "response too big")
    monkeypatch.setattr(CkwClient, "async_get_measurements", fake)
    entry = add_entry(hass)
    coordinator = CkwCoordinator(hass, entry, make_client())

    await coordinator.async_refresh()

    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "even the 3-day window" in str(coordinator.last_exception)
    assert len(fake.calls) == 2
