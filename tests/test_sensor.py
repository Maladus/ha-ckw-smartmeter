"""Tests for the per-meter sensor platform.

The fixtures are replayed through the real config entry, coordinator and sensor
platform: only ``CkwClient.async_get_customer`` and
``CkwClient.async_get_measurements`` are patched, so no socket is opened. Time is
frozen because the rolling window, the yesterday value and the publication delay
all depend on "now".
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ckw import sensor as sensor_module
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
    TIME_ZONE,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIXTURE_DIR: Final[Path] = Path(__file__).parent / "fixtures"
CUSTOMER_ID: Final = "0000000099"
CITY: Final = "Luzern"
IMPORT_METER: Final = "CH1003601234500000000000000000000"
EXPORT_METER: Final = "CH1003601234500000000000000000001"

#: Frozen poll time, winter (CET, UTC+1): 2026-11-02 12:00 local, 11:00 UTC.
FROZEN_NOW: Final = datetime(2026, 11, 2, 12, 0, tzinfo=TIME_ZONE)
FROZEN_NOW_UTC: Final = FROZEN_NOW.astimezone(UTC)


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


def load_series(name: str) -> list[CkwMeterMeasurements]:
    """Replay one committed measurement fixture as the API would return it."""
    document = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return [
        CkwMeterMeasurements(
            deliverypointnumber=deliverypoint,
            measurements=tuple(_measurement(entry) for entry in entries),
        )
        for deliverypoint, entries in document["series"].items()
    ]


def load_customer() -> CkwCustomer:
    """Parse the committed customer fixture with the real API parser."""
    document = json.loads((FIXTURE_DIR / "customer.json").read_text(encoding="utf-8"))
    return _parse_customer(document)


def series_for(
    series: list[CkwMeterMeasurements], deliverypoint: str
) -> tuple[CkwMeasurement, ...]:
    """Return the intervals of one delivery point, or an empty tuple."""
    for item in series:
        if item.deliverypointnumber == deliverypoint:
            return item.measurements
    return ()


class ExpectedMeter:
    """The fixture's numbers, computed independently of the coordinator."""

    def __init__(self, measurements: tuple[CkwMeasurement, ...]) -> None:
        """Sum the complete intervals and remember the newest day."""
        self.kwh_total = 0.0
        self.kwh_ht = 0.0
        self.kwh_nt = 0.0
        self.quality_intervals = 0
        for measurement in measurements:
            if measurement.has_quality_issue:
                self.quality_intervals += 1
                continue
            if measurement.kwh_ht is None or measurement.kwh_nt is None:
                continue
            self.kwh_total += measurement.kwh_ht + measurement.kwh_nt
            self.kwh_ht += measurement.kwh_ht
            self.kwh_nt += measurement.kwh_nt

        newest = max(
            measurements, key=lambda item: item.end_utc or item.start_utc or _utc("1970-01-01")
        )
        self.last_interval_end = newest.end_utc or newest.start_utc
        self.last_day_kwh = None if newest.has_quality_issue else newest.kwh_total
        self.last_day_start = newest.start_utc
        self.publication_delay_h = (
            None
            if self.last_interval_end is None
            else (FROZEN_NOW_UTC - self.last_interval_end).total_seconds() / 3600
        )


class FakeApi:
    """Replay the fixtures through the two client methods the coordinator uses."""

    def __init__(
        self,
        days: list[CkwMeterMeasurements],
        minutes: list[CkwMeterMeasurements],
        customer: CkwCustomer,
    ) -> None:
        self.days = days
        self.minutes = minutes
        self.customer = customer
        self.calls: list[tuple[date, date, str]] = []
        self.error: Exception | None = None


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
        fake.calls.append((start, end, aggregation))
        if fake.error is not None:
            raise fake.error
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


def register_implementation(hass: HomeAssistant) -> None:
    """Register the fake implementation as the only OAuth2 implementation."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, FakeOAuth2Implementation(hass)
    )


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
    """Set up the real integration against the replayed fixtures."""
    register_implementation(hass)
    patch_api(monkeypatch, fake)
    entry = add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def unique_id(entry: ConfigEntry, deliverypoint: str, key: str) -> str:
    """The append-only entity unique id from ADR-0003."""
    return f"{entry.entry_id}_{deliverypoint}_{key}"


def entity_id(hass: HomeAssistant, entry: ConfigEntry, deliverypoint: str, key: str) -> str:
    """Return the registered entity id for one unique id."""
    registered = er.async_get(hass).async_get_entity_id(
        Platform.SENSOR, DOMAIN, unique_id(entry, deliverypoint, key)
    )
    assert registered is not None, f"{deliverypoint} {key} is not registered"
    return registered


def state_of(hass: HomeAssistant, entry: ConfigEntry, deliverypoint: str, key: str) -> Any:
    """Return the state object of one sensor."""
    state = hass.states.get(entity_id(hass, entry, deliverypoint, key))
    assert state is not None
    return state


def fixture_fake() -> FakeApi:
    """The two-meter account: import and export with daily and quarter-hour data."""
    return FakeApi(
        days=load_series("measurements_days.json"),
        minutes=load_series("measurements_minutes.json"),
        customer=load_customer(),
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


# ---------------------------------------------------------------------------
# States and attributes (#76, #77, #80, #82, #83)
# ---------------------------------------------------------------------------


async def test_both_meters_expose_states_and_attributes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import and export each get the four default sensors from the fixture."""
    fake = fixture_fake()

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        data = entry.runtime_data.coordinator.data
        assert data is not None
        assert {IMPORT_METER, EXPORT_METER} <= set(data.meters)

        for deliverypoint in (IMPORT_METER, EXPORT_METER):
            expected = ExpectedMeter(series_for(fake.days, deliverypoint))

            total = state_of(hass, entry, deliverypoint, "consumption_total")
            assert float(total.state) == pytest.approx(expected.kwh_total)
            assert total.attributes["publication_delay_h"] == pytest.approx(
                expected.publication_delay_h
            )
            assert total.attributes["last_published_interval"] == expected.last_interval_end
            assert total.attributes["quality_intervals"] == expected.quality_intervals

            yesterday = state_of(hass, entry, deliverypoint, "consumption_yesterday")
            assert float(yesterday.state) == pytest.approx(expected.last_day_kwh)
            assert expected.last_day_start is not None
            assert yesterday.attributes["last_reset"] == (
                expected.last_day_start.astimezone(TIME_ZONE).isoformat()
            )

            last_update = state_of(hass, entry, deliverypoint, "last_update")
            assert datetime.fromisoformat(last_update.state) == FROZEN_NOW_UTC

            published = state_of(hass, entry, deliverypoint, "last_published_interval")
            assert datetime.fromisoformat(published.state) == expected.last_interval_end

    # The quality flag on the import fixture is exposed, not silently summed.
    assert ExpectedMeter(series_for(fake.days, IMPORT_METER)).quality_intervals == 1
    assert ExpectedMeter(series_for(fake.days, EXPORT_METER)).quality_intervals == 0


async def test_a_meter_without_data_is_unavailable_not_removed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The customer still knows the export meter, so its entities stay registered."""
    fake = FakeApi(
        days=[
            CkwMeterMeasurements(
                deliverypointnumber=IMPORT_METER,
                measurements=series_for(load_series("measurements_days.json"), IMPORT_METER),
            )
        ],
        minutes=[],
        customer=load_customer(),
    )

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        assert entry.runtime_data.coordinator.data is not None
        assert EXPORT_METER not in entry.runtime_data.coordinator.data.meters

        export_total = state_of(hass, entry, EXPORT_METER, "consumption_total")
        assert export_total.state == STATE_UNAVAILABLE
        assert entity_id(hass, entry, EXPORT_METER, "consumption_total") is not None

        # The device is still there, hanging under the service device.
        device_registry = dr.async_get(hass)
        service = device_registry.async_get_device({(DOMAIN, entry.entry_id)})
        device = device_registry.async_get_device(
            {(DOMAIN, f"{entry.entry_id}_{mask_deliverypoint(EXPORT_METER)}")}
        )
        assert service is not None
        assert device is not None
        assert device.via_device_id == service.id


# ---------------------------------------------------------------------------
# Quarter-hour sensor (#152)
# ---------------------------------------------------------------------------


async def test_quarter_hour_is_disabled_by_default_and_shows_the_newest_interval(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabling the opt-in sensor exposes the newest 15-minute interval and its bounds."""
    fake = fixture_fake()

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        registered = er.async_get(hass).async_get_entity_id(
            Platform.SENSOR, DOMAIN, unique_id(entry, IMPORT_METER, "quarter_hour")
        )
        assert registered is not None
        registry_entry = er.async_get(hass).async_get(registered)
        assert registry_entry is not None
        assert registry_entry.disabled
        assert hass.states.get(registered) is None

        er.async_get(hass).async_update_entity(registered, disabled_by=None)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        expected = series_for(fake.minutes, IMPORT_METER)
        newest = max(expected, key=lambda item: item.end_utc or item.start_utc)

        quarter = state_of(hass, entry, IMPORT_METER, "quarter_hour")
        assert float(quarter.state) == pytest.approx(newest.kwh_total or 0.0)
        assert quarter.attributes["interval_start"] == newest.start_utc
        assert quarter.attributes["interval_end"] == newest.end_utc
        assert quarter.attributes["publication_delay_h"] == pytest.approx(
            (FROZEN_NOW_UTC - (newest.end_utc or newest.start_utc)).total_seconds() / 3600
        )


# ---------------------------------------------------------------------------
# Identity and platform constants (#81, #135)
# ---------------------------------------------------------------------------


async def test_unique_ids_are_stable_across_two_setups(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reload keeps every append-only unique id, including the meter and key."""
    fake = fixture_fake()

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        before = {item.unique_id for item in sensor_entries(hass, entry)}
        assert unique_id(entry, IMPORT_METER, "consumption_total") in before
        assert unique_id(entry, EXPORT_METER, "quarter_hour") in before

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        after = {item.unique_id for item in sensor_entries(hass, entry)}

    assert after == before


async def test_every_entity_has_entity_name_and_the_platform_does_not_parallelise(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The meter is the device name prefix, and all entities share one poll."""
    fake = fixture_fake()

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)

    assert sensor_module.PARALLEL_UPDATES == 0
    entries = sensor_entries(hass, entry)
    assert entries
    assert all(item.has_entity_name for item in entries)


# ---------------------------------------------------------------------------
# Helper and availability paths (Silver test-coverage)
# ---------------------------------------------------------------------------


def _totals_fixture(**overrides: Any) -> Any:
    """Build the coordinator data one sensor reads, with overridable totals."""
    from custom_components.ckw.coordinator import CkwData, MeterTotals

    totals = MeterTotals(
        kwh_total=overrides.pop("kwh_total", 3.0),
        kwh_ht=overrides.pop("kwh_ht", 2.0),
        kwh_nt=overrides.pop("kwh_nt", 1.0),
        last_interval_end=overrides.pop("last_interval_end", FROZEN_NOW_UTC),
        publication_delay_h=overrides.pop("publication_delay_h", 1.0),
        intervals_seen=overrides.pop("intervals_seen", 1),
        quality_intervals=overrides.pop("quality_intervals", 0),
        last_day_kwh=overrides.pop("last_day_kwh", 3.0),
        last_day_start=overrides.pop("last_day_start", FROZEN_NOW_UTC),
    )
    return CkwData(
        meters={IMPORT_METER: totals},
        rate_limit=None,
        newest_interval=FROZEN_NOW_UTC,
        polled_at=FROZEN_NOW_UTC,
    )


def test_the_value_helpers_return_none_when_there_is_no_total() -> None:
    """Data, meter and intervals can each be missing; none of them is a zero."""
    from custom_components.ckw import sensor as sensor_module

    data = _totals_fixture()
    empty = _totals_fixture(intervals_seen=0)

    assert sensor_module._totals(None, IMPORT_METER) is None
    assert sensor_module._totals(empty, IMPORT_METER) is None
    assert sensor_module._totals(data, "unknown") is None
    assert sensor_module._consumption_total(data, IMPORT_METER) == 3.0
    assert sensor_module._consumption_yesterday(data, IMPORT_METER) == 3.0
    assert sensor_module._consumption_high_tariff(data, IMPORT_METER) == 2.0
    assert sensor_module._consumption_low_tariff(data, IMPORT_METER) == 1.0
    assert sensor_module._last_published_interval(data, IMPORT_METER) == FROZEN_NOW_UTC


async def test_sensor_setup_without_data_adds_nothing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform forward before the first poll must not create entities."""
    from types import SimpleNamespace

    from custom_components.ckw import sensor as sensor_module

    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(data=None)),
        async_on_unload=lambda callback: None,
    )

    await sensor_module.async_setup_entry(hass, entry, added.extend)

    assert added == []


async def test_the_follow_up_listener_without_data_adds_nothing(hass: HomeAssistant) -> None:
    """The coordinator can clear its data between a callback and its execution."""
    from types import SimpleNamespace

    from custom_components.ckw import sensor as sensor_module
    from custom_components.ckw.coordinator import CkwData

    listeners: list[Any] = []
    coordinator = SimpleNamespace(
        data=CkwData(meters={}, rate_limit=None, newest_interval=None),
        async_add_listener=lambda callback: listeners.append(callback) or (lambda: None),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda callback: None,
    )
    added: list[Any] = []

    await sensor_module.async_setup_entry(hass, entry, added.extend)
    coordinator.data = None
    listeners[0]()

    assert added == []


def test_a_sensor_without_data_has_no_value_attributes_or_stale_mark(
    hass: HomeAssistant,
) -> None:
    """Every read-only property tolerates a coordinator that has no data yet."""
    from types import SimpleNamespace

    from custom_components.ckw import sensor as sensor_module
    from custom_components.ckw.entity import CkwEntity

    entry = add_entry(hass)
    coordinator = SimpleNamespace(config_entry=entry, last_update_success=True, data=None)
    entity = CkwEntity(coordinator, deliverypoint=IMPORT_METER, key="consumption_total")

    assert entity.available is False
    assert entity.stale_since is None

    total = next(
        item for item in sensor_module.SENSOR_DESCRIPTIONS if item.key == "consumption_total"
    )
    yesterday = next(
        item for item in sensor_module.SENSOR_DESCRIPTIONS if item.key == "consumption_yesterday"
    )
    quarter = next(item for item in sensor_module.SENSOR_DESCRIPTIONS if item.key == "quarter_hour")
    sensor = sensor_module.CkwSensor(coordinator, IMPORT_METER, None, total)
    yesterday_sensor = sensor_module.CkwSensor(coordinator, IMPORT_METER, None, yesterday)
    quarter_sensor = sensor_module.CkwSensor(coordinator, IMPORT_METER, None, quarter)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None
    assert yesterday_sensor.last_reset is None
    assert quarter_sensor.extra_state_attributes is None

    quarter_sensor.coordinator.data = _totals_fixture()
    assert quarter_sensor.extra_state_attributes is None


def test_a_total_sensor_without_totals_has_no_attributes(hass: HomeAssistant) -> None:
    """A meter that has no complete interval yet exposes no publication details."""
    from types import SimpleNamespace

    from custom_components.ckw import sensor as sensor_module

    entry = add_entry(hass)
    coordinator = SimpleNamespace(
        config_entry=entry,
        last_update_success=True,
        data=_totals_fixture(intervals_seen=0),
    )
    total = next(
        item for item in sensor_module.SENSOR_DESCRIPTIONS if item.key == "consumption_total"
    )
    sensor = sensor_module.CkwSensor(coordinator, IMPORT_METER, None, total)

    assert sensor.extra_state_attributes is None


def test_an_unknown_meter_model_is_kept_as_the_raw_string() -> None:
    """A model CKW invents later must still be shown, not crash the device page."""
    from custom_components.ckw.api import CkwMeter
    from custom_components.ckw.entity import _meter_model

    unknown = CkwMeter(
        id="1",
        model="A brand new model",
        type=None,
        fuel=None,
        deliverypointnumber=IMPORT_METER,
        supply_start=None,
        supply_end=None,
    )

    assert _meter_model(None) is None
    assert _meter_model(unknown) == "A brand new model"


def test_an_entity_needs_a_config_entry() -> None:
    """A coordinator created without an entry cannot build an append-only id."""
    from types import SimpleNamespace

    from custom_components.ckw.entity import CkwEntity

    coordinator = SimpleNamespace(config_entry=None, last_update_success=True, data=None)

    with pytest.raises(ValueError):
        CkwEntity(coordinator, deliverypoint=IMPORT_METER, key="consumption_total")


async def test_a_failed_poll_makes_every_sensor_unavailable_and_recovery_restores_it(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Availability follows the coordinator, not only the meter's presence."""
    from custom_components.ckw import statistics as statistics_module
    from custom_components.ckw.api import CkwConnectionError

    fake = fixture_fake()
    fake.error = None
    monkeypatch.setattr(statistics_module, "async_import_statistics", lambda *args: None)

    with freeze_time(FROZEN_NOW):
        entry = await setup_entry(hass, monkeypatch, fake)
        assert state_of(hass, entry, IMPORT_METER, "consumption_total").state != STATE_UNAVAILABLE

        fake.error = CkwConnectionError("offline")
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

        assert state_of(hass, entry, IMPORT_METER, "consumption_total").state == STATE_UNAVAILABLE

        fake.error = None
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

        assert state_of(hass, entry, IMPORT_METER, "consumption_total").state != STATE_UNAVAILABLE
