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
    CkwApiError,
    CkwAuthError,
    CkwClient,
    CkwConnectionError,
    CkwError,
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
    # The message is translated in the frontend; code and tests assert the key (#130).
    assert error.translation_key == "rate_limited"
    assert error.translation_placeholders == {"reset": "120 s"}
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
    assert coordinator.last_exception.translation_key == "range_too_big"
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# Failure mapping and defensive paths (#70, Silver test-coverage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (CkwApiError(503, "busy"), "api_error"),
        (CkwError("a new answer"), "unknown"),
        (CkwRateLimitError("quota"), "rate_limited_no_reset"),
    ],
)
async def test_api_and_unknown_failures_become_translated_update_failures(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    translation_key: str,
) -> None:
    """Every ``api.py`` failure has a key, including one without a reset time."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    fake = FakeMeasurements()
    fake.error = error
    monkeypatch.setattr(coordinator.client, "async_get_measurements", fake)

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_fetch(date(2026, 10, 26), date(2026, 11, 1))

    assert raised.value.translation_key == translation_key


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ConfigEntryAuthFailed),
        (403, ConfigEntryAuthFailed),
        (503, UpdateFailed),
    ],
)
async def test_a_raw_aiohttp_response_error_is_mapped_by_status(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: type[Exception],
) -> None:
    """The token refresh can answer before ``api.py`` sees the response."""
    from unittest.mock import Mock

    import aiohttp

    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    fake = FakeMeasurements()
    fake.error = aiohttp.ClientResponseError(Mock(), (), status=status)
    monkeypatch.setattr(coordinator.client, "async_get_measurements", fake)

    with pytest.raises(expected):
        await coordinator._async_fetch(date(2026, 10, 26), date(2026, 11, 1))


@pytest.mark.parametrize(
    "error",
    [TimeoutError("slow"), __import__("aiohttp").ClientConnectionError("offline")],
)
async def test_timeouts_and_transport_errors_become_cannot_connect(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """A request without a response is a connection failure, never a traceback."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    fake = FakeMeasurements()
    fake.error = error
    monkeypatch.setattr(coordinator.client, "async_get_measurements", fake)

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_fetch(date(2026, 10, 26), date(2026, 11, 1))

    assert raised.value.translation_key == "cannot_connect"


async def test_pacing_skips_the_wait_when_there_is_nothing_to_wait_for(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No quota, a healthy quota and an expired reset all send immediately."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(coordinator_module, "asyncio", SimpleNamespace(sleep=fake_sleep))

    coordinator.client.last_rate_limit = RateLimitInfo(limit=4, remaining=None, reset_ms=None)
    await coordinator.async_pace()

    coordinator.client.last_rate_limit = RateLimitInfo(limit=4, remaining=2, reset_ms=1000)
    await coordinator.async_pace()

    coordinator.client.last_rate_limit = RateLimitInfo(limit=4, remaining=0, reset_ms=None)
    await coordinator.async_pace()

    coordinator.client.last_rate_limit = RateLimitInfo(limit=4, remaining=0, reset_ms=1000)
    coordinator._rate_limit_info = coordinator.client.last_rate_limit
    coordinator._rate_limit_at = time.monotonic() - 5
    await coordinator.async_pace()

    assert sleeps == []


async def test_merge_ignores_series_and_intervals_without_identity_or_time(
    hass: HomeAssistant,
) -> None:
    """A nameless series and a timeless interval never enter the totals."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
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

    counts, corrected, newest = coordinator._merge(
        [
            CkwMeterMeasurements(deliverypointnumber="", measurements=(timeless,)),
            CkwMeterMeasurements(deliverypointnumber=ENTRY_METER, measurements=(timeless,)),
        ]
    )

    assert counts == {ENTRY_METER: 0}
    assert corrected == 0
    assert newest is None


async def test_a_corrected_interval_replaces_the_stored_one(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """CKW's later answer for the same end wins and is logged at debug."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    original = load_series()[0].measurements[-1]
    corrected = CkwMeasurement(
        start_utc=original.start_utc,
        end_utc=original.end_utc,
        kwh_ht=9.0,
        kwh_nt=9.0,
        chf_ht=None,
        chf_nt=None,
        lines_p=1,
        lines_fw=1,
        lines_fb=1,
    )
    deliverypoint = load_series()[0].deliverypointnumber
    coordinator._merge(
        [CkwMeterMeasurements(deliverypointnumber=deliverypoint, measurements=(original,))]
    )
    caplog.set_level(logging.DEBUG, logger=COORDINATOR_LOGGER)

    counts, corrected_count, _ = coordinator._merge(
        [CkwMeterMeasurements(deliverypointnumber=deliverypoint, measurements=(corrected,))]
    )

    assert counts[deliverypoint] == 0
    assert corrected_count == 1
    assert any("corrected the interval" in record.getMessage() for record in caplog.records)


async def test_a_poll_without_a_publication_timestamp_logs_nothing(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A meter with no published interval has no baseline to log about."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    caplog.set_level(logging.INFO, logger=COORDINATOR_LOGGER)

    coordinator._log_publication(
        coordinator_module.MeterTotals(
            kwh_total=0.0,
            kwh_ht=0.0,
            kwh_nt=0.0,
            last_interval_end=None,
            publication_delay_h=None,
            intervals_seen=0,
            quality_intervals=0,
        ),
        FROZEN_NOW,
    )

    assert caplog.records == []


async def test_repair_checks_are_skipped_without_a_config_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coordinator detached from its entry never fails a poll on repairs."""
    called = AsyncMock()
    monkeypatch.setattr(coordinator_module, "async_check_repairs", called)
    coordinator = SimpleNamespace(hass=hass, config_entry=None)

    await CkwCoordinator._async_check_repairs(coordinator, SimpleNamespace())  # type: ignore[arg-type]

    called.assert_not_awaited()


async def test_a_failed_quarter_hour_request_leaves_the_daily_data_alone(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opt-in quarter-hour request is never allowed to fail the poll."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
    fake = FakeMeasurements()
    fake.error = CkwConnectionError("offline")
    monkeypatch.setattr(coordinator.client, "async_get_measurements", fake)

    assert await coordinator._async_fetch_quarter_hour(FROZEN_NOW) == {}


async def test_quarter_hour_rows_without_a_meter_or_a_timestamp_are_ignored(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a complete, named interval can become the quarter-hour sensor."""
    coordinator = CkwCoordinator(hass, add_entry(hass), make_client())
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
    fake = FakeMeasurements(
        [
            CkwMeterMeasurements(deliverypointnumber="", measurements=(timeless,)),
            CkwMeterMeasurements(deliverypointnumber=ENTRY_METER, measurements=(timeless,)),
        ]
    )
    monkeypatch.setattr(coordinator.client, "async_get_measurements", fake)

    assert await coordinator._async_fetch_quarter_hour(FROZEN_NOW) == {}


def test_totals_skip_an_incomplete_interval_instead_of_counting_it_as_zero() -> None:
    """Unknown is not free electricity, so the sum ignores that interval."""
    from custom_components.ckw.coordinator import _totals_for

    end = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
    incomplete = CkwMeasurement(
        start_utc=end - timedelta(days=1),
        end_utc=end,
        kwh_ht=2.0,
        kwh_nt=None,
        chf_ht=None,
        chf_nt=None,
        lines_p=1,
        lines_fw=1,
        lines_fb=1,
    )

    totals = _totals_for({end: incomplete}, FROZEN_NOW)

    assert totals.kwh_total == 0.0
    assert totals.intervals_seen == 1
    assert totals.last_day_kwh is None


def test_the_newest_quarter_hour_skips_a_row_without_an_end() -> None:
    """A timeless row cannot be the newest complete quarter hour."""
    from custom_components.ckw.coordinator import _newest_quarter_hour

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

    assert _newest_quarter_hour((timeless,)) is None


def test_meter_info_keeps_only_meters_with_a_delivery_point() -> None:
    """A meter without a Zählpunkt cannot be keyed or shown."""
    from custom_components.ckw.api import CkwCustomer, CkwLocation, CkwMeter
    from custom_components.ckw.coordinator import _meter_info

    named = CkwMeter(
        id="1",
        model=None,
        type=None,
        fuel=None,
        deliverypointnumber=ENTRY_METER,
        supply_start=None,
        supply_end=None,
    )
    unnamed = CkwMeter(
        id="2",
        model=None,
        type=None,
        fuel=None,
        deliverypointnumber="",
        supply_start=None,
        supply_end=None,
    )
    customer = CkwCustomer(
        id=CUSTOMER_ID,
        locations=(
            CkwLocation(
                id="1",
                gpnr="1",
                street="Musterweg 1",
                postal_code="6000",
                city=CITY,
                meters=(named, unnamed),
            ),
        ),
    )

    assert set(_meter_info(customer)) == {ENTRY_METER}
