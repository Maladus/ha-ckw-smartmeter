"""Tests for dynamic and stale delivery points (#134).

The answers are replayed through the real config entry, coordinator and sensor
platform: only ``CkwClient.async_get_customer`` and
``CkwClient.async_get_measurements`` are patched, so no socket is opened. The
daily rows are built relative to "now" instead of being read from the committed
fixtures, because these tests move the clock across the days a meter has to be
missing before it is stale - and the entry's own meter has to keep publishing
meanwhile, or the publication checks would fire as well.

Only the customer payload comes from ``tests/fixtures/customer.json``, so the
devices keep the meter models and the gas meter the other tests use. Statistics
need a recorder, which these tests do not start: the import itself is covered by
``test_statistics.py``, so ``async_import_statistics`` is a no-op here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw import async_remove_config_entry_device
from custom_components.ckw import statistics as statistics_module
from custom_components.ckw.api import (
    CkwClient,
    CkwCustomer,
    CkwMeasurement,
    CkwMeterMeasurements,
    _parse_customer,
    mask_deliverypoint,
)
from custom_components.ckw.const import (
    CONF_CITY,
    CONF_CUSTOMER_ID,
    CONF_METER,
    DAYS_AGGREGATION,
    DOMAIN,
    MINUTES_AGGREGATION,
    STALE_AFTER_DAYS,
    TIME_ZONE,
)
from custom_components.ckw.sensor import SENSOR_DESCRIPTIONS

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIXTURE_DIR: Final[Path] = Path(__file__).parent / "fixtures"
CUSTOMER_ID: Final = "0000000099"
CITY: Final = "Luzern"
IMPORT_METER: Final = "CH1003601234500000000000000000000"
EXPORT_METER: Final = "CH1003601234500000000000000000001"
GAS_METER: Final = "CH1003601234500000000000000000002"

#: Frozen poll time, winter (CET, UTC+1): 2026-11-02 12:00 local, 11:00 UTC.
FROZEN_NOW: Final = datetime(2026, 11, 2, 12, 0, tzinfo=TIME_ZONE)
ONE_DAY: Final = timedelta(days=1)
KWH_PER_DAY: Final = 1.5
LOGGER: Final = "custom_components.ckw.coordinator"


@pytest.fixture(autouse=True)
def statistics_without_a_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the recorder out of these device tests (see the module docstring)."""

    def _ignore(*args: Any, **kwargs: Any) -> None:
        """Pretend the statistics rows were written."""

    monkeypatch.setattr(statistics_module, "async_import_statistics", _ignore)


# ---------------------------------------------------------------------------
# Rows and payloads the fake API returns
# ---------------------------------------------------------------------------


def daily_series(deliverypoint: str, now: datetime, *, days: int = 3) -> CkwMeterMeasurements:
    """Build complete daily intervals ending yesterday, so no check fires."""
    today = now.astimezone(TIME_ZONE).date()
    measurements = []
    for days_ago in range(days, 0, -1):
        day = today - timedelta(days=days_ago)
        start = datetime(day.year, day.month, day.day, tzinfo=TIME_ZONE)
        measurements.append(
            CkwMeasurement(
                start_utc=start.astimezone(UTC),
                end_utc=(start + ONE_DAY).astimezone(UTC),
                kwh_ht=KWH_PER_DAY / 2,
                kwh_nt=KWH_PER_DAY / 2,
                chf_ht=None,
                chf_nt=None,
                lines_p=1,
                lines_fw=1,
                lines_fb=1,
            )
        )
    return CkwMeterMeasurements(deliverypointnumber=deliverypoint, measurements=tuple(measurements))


def customer_for(*deliverypoints: str) -> CkwCustomer:
    """The committed customer, restricted to the meters the test says exist."""
    document = json.loads((FIXTURE_DIR / "customer.json").read_text(encoding="utf-8"))
    customer = _parse_customer(document)
    wanted = set(deliverypoints)
    return replace(
        customer,
        locations=tuple(
            replace(
                location,
                meters=tuple(
                    meter for meter in location.meters if meter.deliverypointnumber in wanted
                ),
            )
            for location in customer.locations
        ),
    )


class FakeApi:
    """Replay a mutable answer through the two client methods the coordinator uses."""

    def __init__(
        self,
        days: list[CkwMeterMeasurements],
        customer: CkwCustomer,
        minutes: list[CkwMeterMeasurements] | None = None,
    ) -> None:
        self.days = days
        self.minutes = minutes or []
        self.customer = customer


def patch_api(monkeypatch: pytest.MonkeyPatch, fake: FakeApi) -> None:
    """Patch the two CKW calls without opening a socket."""

    async def get_customer(_client: CkwClient) -> CkwCustomer:
        return fake.customer

    async def get_measurements(
        _client: CkwClient,
        start: date,
        end: date,
        aggregation: str = DAYS_AGGREGATION,
    ) -> list[CkwMeterMeasurements]:
        if aggregation == MINUTES_AGGREGATION:
            return fake.minutes
        return fake.days

    monkeypatch.setattr(CkwClient, "async_get_customer", get_customer)
    monkeypatch.setattr(CkwClient, "async_get_measurements", get_measurements)


# ---------------------------------------------------------------------------
# Config entry and OAuth2 helpers
# ---------------------------------------------------------------------------


class FakeOAuth2Implementation(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    """Local OAuth2 implementation that hands out one fixed token pair."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Store the Home Assistant instance."""
        self.hass = hass

    @property
    def name(self) -> str:
        """Return the implementation name."""
        return "CKW test IdP"

    @property
    def domain(self) -> str:
        """Return the auth implementation domain stored in the entry."""
        return DOMAIN

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Return a URL the test never opens."""
        return "https://id.example.invalid/authorize"

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Return the token pair the callback would resolve to."""
        return {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    async def _async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """Return a refreshed token pair."""
        return {
            **token,
            "access_token": "refreshed-access-token",
            "refresh_token": "refreshed-refresh-token",
            "expires_in": 3600,
        }


def add_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a configured CKW entry with a still-valid token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CUSTOMER_ID: CUSTOMER_ID,
            CONF_CITY: CITY,
            CONF_METER: IMPORT_METER,
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "token_type": "Bearer",
            },
        },
        unique_id=CUSTOMER_ID,
        title=f"{CUSTOMER_ID} — {CITY}",
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, fake: FakeApi
) -> ConfigEntry:
    """Set up the real integration against the replayed answers."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, FakeOAuth2Implementation(hass)
    )
    patch_api(monkeypatch, fake)
    entry = add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# Registry and clock helpers
# ---------------------------------------------------------------------------


def unique_id(entry: ConfigEntry, deliverypoint: str, key: str) -> str:
    """The append-only entity unique id from ADR-0003."""
    return f"{entry.entry_id}_{deliverypoint}_{key}"


def registered_entity_id(
    hass: HomeAssistant, entry: ConfigEntry, deliverypoint: str, key: str
) -> str | None:
    """Return the registered entity id for one unique id, or ``None``."""
    return er.async_get(hass).async_get_entity_id(
        Platform.SENSOR, DOMAIN, unique_id(entry, deliverypoint, key)
    )


def sensor_entries(hass: HomeAssistant, entry: ConfigEntry) -> list[er.RegistryEntry]:
    """Return the CKW sensor entities registered for one config entry."""
    return [
        item
        for item in er.async_get(hass).entities.values()
        if item.config_entry_id == entry.entry_id
        and item.domain == Platform.SENSOR
        and item.platform == DOMAIN
    ]


def meter_device(
    hass: HomeAssistant, entry: ConfigEntry, deliverypoint: str
) -> dr.DeviceEntry | None:
    """Return one meter's device from the device registry."""
    return dr.async_get(hass).async_get_device(
        {(DOMAIN, f"{entry.entry_id}_{mask_deliverypoint(deliverypoint)}")}
    )


def service_device(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry:
    """Return the entry's service device, which always exists after setup."""
    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device is not None
    return device


async def poll(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Run one poll and let the platform's entity additions finish."""
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def poll_at(hass: HomeAssistant, entry: ConfigEntry, now: datetime) -> None:
    """Run one poll with the clock frozen at ``now``."""
    with freeze_time(now):
        await poll(hass, entry)


def issue_ids(hass: HomeAssistant) -> list[str]:
    """Return the ids of the repair issues of this domain."""
    return [issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN]


def state_of(hass: HomeAssistant, entry: ConfigEntry, deliverypoint: str, key: str) -> Any:
    """Return the state object of one sensor, which must be registered."""
    entity_id = registered_entity_id(hass, entry, deliverypoint, key)
    assert entity_id is not None, f"{deliverypoint} {key} is not registered"
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def entity_of(hass: HomeAssistant, entry: ConfigEntry, deliverypoint: str, key: str) -> Entity:
    """Return the entity object behind one sensor.

    Home Assistant drops an entity's attributes from the state machine while it
    is unavailable (``Entity.__async_calculate_state``), so ``stale_since`` of a
    stale meter is read here, on the entity the attribute belongs to.
    """
    entity_id = registered_entity_id(hass, entry, deliverypoint, key)
    assert entity_id is not None, f"{deliverypoint} {key} is not registered"
    component = hass.data["entity_components"][Platform.SENSOR]
    entity = component.get_entity(entity_id)
    assert entity is not None
    return entity


# ---------------------------------------------------------------------------
# Dynamic addition (#134)
# ---------------------------------------------------------------------------


async def test_a_new_delivery_point_gets_its_entities_without_a_reload(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A meter the portal adds after setup shows up on the next poll."""
    fake = FakeApi(
        days=[daily_series(IMPORT_METER, FROZEN_NOW)],
        customer=customer_for(IMPORT_METER, GAS_METER),
    )

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        coordinator = entry.runtime_data.coordinator
        assert len(sensor_entries(hass, entry)) == len(SENSOR_DESCRIPTIONS)
        assert registered_entity_id(hass, entry, EXPORT_METER, "consumption_total") is None
        assert meter_device(hass, entry, EXPORT_METER) is None

    # The second delivery point appears in the portal.
    fake.days = [
        daily_series(IMPORT_METER, FROZEN_NOW),
        daily_series(EXPORT_METER, FROZEN_NOW),
    ]
    fake.customer = customer_for(IMPORT_METER, EXPORT_METER, GAS_METER)
    await poll_at(hass, entry, FROZEN_NOW)

    # No reload happened: the entry kept its coordinator and its state.
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator is coordinator
    assert len(sensor_entries(hass, entry)) == 2 * len(SENSOR_DESCRIPTIONS)

    total = state_of(hass, entry, EXPORT_METER, "consumption_total")
    assert float(total.state) == pytest.approx(3 * KWH_PER_DAY)

    # The device was registered on the fly, under the entry's service device.
    device = meter_device(hass, entry, EXPORT_METER)
    assert device is not None
    assert device.via_device_id == service_device(hass, entry).id
    assert device.model == "SMART"


async def test_repeating_one_poll_adds_no_duplicate_entities(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three polls with the same answer keep one entity per meter and key."""
    fake = FakeApi(
        days=[daily_series(IMPORT_METER, FROZEN_NOW)],
        customer=customer_for(IMPORT_METER),
    )

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
    fake.days = [
        daily_series(IMPORT_METER, FROZEN_NOW),
        daily_series(EXPORT_METER, FROZEN_NOW),
    ]
    fake.customer = customer_for(IMPORT_METER, EXPORT_METER)

    for _ in range(3):
        await poll_at(hass, entry, FROZEN_NOW)

    entries = sensor_entries(hass, entry)
    assert len(entries) == 2 * len(SENSOR_DESCRIPTIONS)
    assert len({item.unique_id for item in entries}) == len(entries)
    assert len({item.entity_id for item in entries}) == len(entries)


# ---------------------------------------------------------------------------
# Stale marking (#134)
# ---------------------------------------------------------------------------


async def test_a_vanished_meter_is_stale_after_the_threshold_and_logged_once(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The vanished meter keeps its entities, goes unavailable and is no repair."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    fake = FakeApi(
        days=[
            daily_series(IMPORT_METER, FROZEN_NOW),
            daily_series(EXPORT_METER, FROZEN_NOW),
        ],
        customer=customer_for(IMPORT_METER, EXPORT_METER),
    )
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
    assert issue_ids(hass) == []
    caplog.clear()

    vanished_at: datetime | None = None
    for day in range(1, STALE_AFTER_DAYS + 3):
        now = FROZEN_NOW + day * ONE_DAY
        # Only the entry's own meter keeps publishing: that is the meter the
        # publication checks look at.
        fake.days = [daily_series(IMPORT_METER, now)]
        fake.customer = customer_for(IMPORT_METER)
        await poll_at(hass, entry, now)
        if vanished_at is None:
            data = entry.runtime_data.coordinator.data
            assert data is not None
            vanished_at = data.polled_at

        state = state_of(hass, entry, EXPORT_METER, "consumption_total")
        assert state.state == STATE_UNAVAILABLE
        attributes = entity_of(
            hass, entry, EXPORT_METER, "consumption_total"
        ).extra_state_attributes
        assert attributes is not None

        stale_lines = [r for r in caplog.records if "stale" in r.getMessage()]
        missing_for = now.astimezone(UTC) - vanished_at
        if missing_for <= timedelta(days=STALE_AFTER_DAYS):
            assert "stale_since" not in attributes
            assert stale_lines == []
        else:
            assert attributes["stale_since"] == vanished_at
            assert len(stale_lines) == 1
            assert stale_lines[0].levelno == logging.INFO

    # A stale meter is not a repair issue: CKW can list it again.
    assert issue_ids(hass) == []
    # The device stays registered, so the user still sees it and can delete it.
    assert meter_device(hass, entry, EXPORT_METER) is not None


async def test_a_stale_meter_that_returns_is_available_again(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same meter coming back clears the marking and logs one INFO line."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    fake = FakeApi(
        days=[
            daily_series(IMPORT_METER, FROZEN_NOW),
            daily_series(EXPORT_METER, FROZEN_NOW),
        ],
        customer=customer_for(IMPORT_METER, EXPORT_METER),
    )
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
    total_before = float(state_of(hass, entry, EXPORT_METER, "consumption_total").state)

    # Gone: one poll that misses it, then long enough for it to be stale.
    missed = FROZEN_NOW + ONE_DAY
    fake.days = [daily_series(IMPORT_METER, missed)]
    fake.customer = customer_for(IMPORT_METER)
    await poll_at(hass, entry, missed)
    later = missed + (STALE_AFTER_DAYS + 1) * ONE_DAY
    fake.days = [daily_series(IMPORT_METER, later)]
    await poll_at(hass, entry, later)
    stale = state_of(hass, entry, EXPORT_METER, "consumption_total")
    assert stale.state == STATE_UNAVAILABLE
    stale_attributes = entity_of(
        hass, entry, EXPORT_METER, "consumption_total"
    ).extra_state_attributes
    assert stale_attributes is not None
    assert stale_attributes["stale_since"] is not None
    caplog.clear()

    # And back, with its entities, its history and its totals still attached.
    fake.days = [
        daily_series(IMPORT_METER, later),
        daily_series(EXPORT_METER, later),
    ]
    fake.customer = customer_for(IMPORT_METER, EXPORT_METER)
    await poll_at(hass, entry, later)

    back = state_of(hass, entry, EXPORT_METER, "consumption_total")
    assert back.state != STATE_UNAVAILABLE
    assert "stale_since" not in back.attributes
    back_attributes = entity_of(
        hass, entry, EXPORT_METER, "consumption_total"
    ).extra_state_attributes
    assert back_attributes is None or "stale_since" not in back_attributes
    assert float(back.state) > total_before
    returns = [r for r in caplog.records if "is back" in r.getMessage()]
    assert len(returns) == 1
    assert returns[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# A deleted stale device stays deleted until its meter returns (#134)
# ---------------------------------------------------------------------------


async def test_a_deleted_device_is_only_registered_again_when_its_meter_is_present(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a vanished meter's device sticks, and the meter's return undoes it."""
    fake = FakeApi(
        days=[
            daily_series(IMPORT_METER, FROZEN_NOW),
            daily_series(EXPORT_METER, FROZEN_NOW),
        ],
        customer=customer_for(IMPORT_METER, EXPORT_METER),
    )
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
    device = meter_device(hass, entry, EXPORT_METER)
    assert device is not None
    removed_id = device.id

    # A meter the API still reports keeps its device: removing it is refused.
    assert not await async_remove_config_entry_device(hass, entry, device)
    # The service device belongs to the entry, never to a meter.
    assert not await async_remove_config_entry_device(hass, entry, service_device(hass, entry))

    # The meter vanishes - the portal still lists it, the measurements stop - so
    # the user may delete the device by hand. Home Assistant then drops the
    # device and the entities that hang off it.
    now = FROZEN_NOW + ONE_DAY
    fake.days = [daily_series(IMPORT_METER, now)]
    await poll_at(hass, entry, now)
    assert await async_remove_config_entry_device(hass, entry, device)
    dr.async_get(hass).async_update_device(removed_id, remove_config_entry_id=entry.entry_id)
    await hass.async_block_till_done()
    assert meter_device(hass, entry, EXPORT_METER) is None
    assert registered_entity_id(hass, entry, EXPORT_METER, "consumption_total") is None

    # More polls without the meter: the device is not registered again.
    for day in range(2, 4):
        later = FROZEN_NOW + day * ONE_DAY
        fake.days = [daily_series(IMPORT_METER, later)]
        await poll_at(hass, entry, later)
        assert meter_device(hass, entry, EXPORT_METER) is None
        assert registered_entity_id(hass, entry, EXPORT_METER, "consumption_total") is None

    # Neither does a reload: the tombstone of the removed device is what
    # remembers the decision, even though the portal still lists the meter.
    later = FROZEN_NOW + 3 * ONE_DAY
    fake.days = [daily_series(IMPORT_METER, later)]
    with freeze_time(later):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert meter_device(hass, entry, EXPORT_METER) is None
    assert registered_entity_id(hass, entry, EXPORT_METER, "consumption_total") is None

    # The meter is reported again: its entities and its device come back.
    later = FROZEN_NOW + 4 * ONE_DAY
    fake.days = [
        daily_series(IMPORT_METER, later),
        daily_series(EXPORT_METER, later),
    ]
    await poll_at(hass, entry, later)

    restored = meter_device(hass, entry, EXPORT_METER)
    assert restored is not None
    assert restored.id == removed_id
    assert state_of(hass, entry, EXPORT_METER, "consumption_total").state != STATE_UNAVAILABLE


async def test_a_vanished_meters_entities_survive_a_reload(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reload restores the vanished meter's entities, unavailable and intact."""
    fake = FakeApi(
        days=[
            daily_series(IMPORT_METER, FROZEN_NOW),
            daily_series(EXPORT_METER, FROZEN_NOW),
        ],
        customer=customer_for(IMPORT_METER, EXPORT_METER),
    )
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        fake.days = [daily_series(IMPORT_METER, FROZEN_NOW)]
        fake.customer = customer_for(IMPORT_METER)
        await poll(hass, entry)

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    state = state_of(hass, entry, EXPORT_METER, "consumption_total")
    assert state.state == STATE_UNAVAILABLE
    # The meter vanished in this session only, so it is not stale yet.
    assert "stale_since" not in state.attributes
    assert meter_device(hass, entry, EXPORT_METER) is not None
    # The gas meter in the customer payload still gets no entities.
    assert registered_entity_id(hass, entry, GAS_METER, "consumption_total") is None


async def test_a_meter_the_portal_lists_without_data_goes_stale_too(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A listed meter CKW never publishes data for is stale after the threshold.

    This is the case a restart leaves no interval memory for: the delivery point
    is still in the customer payload, so its entities keep being restored, and
    the absence clock has to come from the poll itself.
    """
    caplog.set_level(logging.INFO, logger=LOGGER)
    fake = FakeApi(
        days=[
            daily_series(IMPORT_METER, FROZEN_NOW),
            daily_series(EXPORT_METER, FROZEN_NOW),
        ],
        customer=customer_for(IMPORT_METER, EXPORT_METER),
    )
    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)

    # The portal keeps listing the meter, the measurement response stops carrying it.
    for day in range(1, STALE_AFTER_DAYS + 3):
        now = FROZEN_NOW + day * ONE_DAY
        fake.days = [daily_series(IMPORT_METER, now)]
        await poll_at(hass, entry, now)

    assert state_of(hass, entry, EXPORT_METER, "consumption_total").state == STATE_UNAVAILABLE
    stale_lines = [r for r in caplog.records if "has been missing" in r.getMessage()]
    assert len(stale_lines) == 1
    assert stale_lines[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# Defensive removal and unload paths (Silver test-coverage)
# ---------------------------------------------------------------------------


async def test_a_device_cannot_be_removed_before_the_first_poll(
    hass: HomeAssistant,
) -> None:
    """Without data no meter can be proven absent, so removal is refused."""
    from types import SimpleNamespace

    entry = add_entry(hass)
    entry.runtime_data = SimpleNamespace(coordinator=SimpleNamespace(data=None))
    device = SimpleNamespace(
        identifiers={(DOMAIN, f"{entry.entry_id}_{mask_deliverypoint(IMPORT_METER)}")}
    )

    assert not await async_remove_config_entry_device(hass, entry, device)


async def test_a_failed_platform_unload_keeps_the_runtime_data(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry stays usable when a platform refuses to unload."""
    from custom_components.ckw import async_unload_entry

    entry = await setup_entry(
        hass,
        monkeypatch,
        FakeApi(
            days=[daily_series(IMPORT_METER, FROZEN_NOW)],
            customer=customer_for(IMPORT_METER),
        ),
    )
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    )

    assert await async_unload_entry(hass, entry) is False
    assert hasattr(entry, "runtime_data")
