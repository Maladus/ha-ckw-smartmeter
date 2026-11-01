"""Sensors for each CKW delivery point.

One platform serves every meter the coordinator reports: four enabled sensors
(cumulative consumption, the newest complete day, the last poll, the newest
published interval) and three opt-in details (HT, NT and the newest quarter
hour). The numbers come from ``coordinator.data`` only; the entities never call
CKW themselves (``docs/reference/conventions.md``).

The platform also follows the coordinator after setup. A delivery point CKW
starts to return gets its entities from that listener, without a config-entry
reload (#134), and a delivery point that vanishes keeps them. Devices are only
registered while their meter is present, so deleting a stale device by hand is
not undone by the next poll.

``consumption_yesterday`` is a ``TOTAL`` sensor with a ``last_reset`` at the
local start of the day it reports, because CKW publishes 1-3 days late and the
newest complete day is what the API actually knows (ADR-0004, ADR-0009).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, Platform, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import CkwMeter
from .const import DOMAIN, TIME_ZONE, Fuel
from .coordinator import CkwCoordinator, CkwData, MeterTotals
from .entity import CkwEntity, meter_device_was_removed, meter_entity_unique_id

if TYPE_CHECKING:
    from . import CkwConfigEntry

__all__ = ["PARALLEL_UPDATES", "SENSOR_DESCRIPTIONS", "async_setup_entry"]

# Every entity reads the same coordinator data, so there is no per-entity I/O
# to serialise and a poll must never be multiplied by the number of sensors.
PARALLEL_UPDATES = 0

type CkwValueFn = Callable[[CkwData, str], float | datetime | None]


@dataclass(frozen=True, kw_only=True)
class CkwSensorEntityDescription(SensorEntityDescription):
    """A sensor description that knows how to read its value from the poll."""

    value_fn: CkwValueFn


def _totals(data: CkwData | None, deliverypoint: str) -> MeterTotals | None:
    """Return the meter's totals, or ``None`` when CKW has no data for it yet."""
    if data is None:
        return None
    totals = data.meters.get(deliverypoint)
    if totals is None or totals.intervals_seen == 0:
        return None
    return totals


def _consumption_total(data: CkwData, deliverypoint: str) -> float | None:
    """Cumulative billed kWh since the integration first saw this meter."""
    totals = _totals(data, deliverypoint)
    return None if totals is None else totals.kwh_total


def _consumption_yesterday(data: CkwData, deliverypoint: str) -> float | None:
    """kWh of the newest complete day CKW has published."""
    totals = _totals(data, deliverypoint)
    return None if totals is None else totals.last_day_kwh


def _consumption_high_tariff(data: CkwData, deliverypoint: str) -> float | None:
    """Cumulative billed kWh in the high-tariff bucket."""
    totals = _totals(data, deliverypoint)
    return None if totals is None else totals.kwh_ht


def _consumption_low_tariff(data: CkwData, deliverypoint: str) -> float | None:
    """Cumulative billed kWh in the low-tariff bucket."""
    totals = _totals(data, deliverypoint)
    return None if totals is None else totals.kwh_nt


def _last_update(data: CkwData, _deliverypoint: str) -> datetime | None:
    """When the coordinator's last successful poll happened."""
    return data.polled_at


def _last_published_interval(data: CkwData, deliverypoint: str) -> datetime | None:
    """Newest ``zeitstempel_bis_utc`` of the meter's daily intervals."""
    totals = _totals(data, deliverypoint)
    return None if totals is None else totals.last_interval_end


def _quarter_hour(data: CkwData, deliverypoint: str) -> float | None:
    """The newest complete 15-minute interval of the current local day."""
    reading = data.quarter_hour.get(deliverypoint)
    return None if reading is None else reading.kwh


SENSOR_DESCRIPTIONS: Final[tuple[CkwSensorEntityDescription, ...]] = (
    CkwSensorEntityDescription(
        key="consumption_total",
        translation_key="consumption_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        entity_registry_enabled_default=True,
        value_fn=_consumption_total,
    ),
    CkwSensorEntityDescription(
        key="consumption_yesterday",
        translation_key="consumption_yesterday",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        entity_registry_enabled_default=True,
        value_fn=_consumption_yesterday,
    ),
    CkwSensorEntityDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        value_fn=_last_update,
    ),
    CkwSensorEntityDescription(
        key="last_published_interval",
        translation_key="last_published_interval",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        value_fn=_last_published_interval,
    ),
    CkwSensorEntityDescription(
        key="consumption_high_tariff",
        translation_key="consumption_high_tariff",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        value_fn=_consumption_high_tariff,
    ),
    CkwSensorEntityDescription(
        key="consumption_low_tariff",
        translation_key="consumption_low_tariff",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        value_fn=_consumption_low_tariff,
    ),
    CkwSensorEntityDescription(
        key="quarter_hour",
        translation_key="quarter_hour",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        value_fn=_quarter_hour,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CkwConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one set of sensors per delivery point and follow the coordinator.

    The first set covers every meter the entry knows, present or not, because
    restoring an unavailable meter's entities is what keeps its history and its
    statistics attached. After that the platform only *adds*. A delivery point
    the poll starts to return gets its entities from the coordinator listener,
    without a reload (#134), and a meter that vanished keeps the entities it has.

    A device is registered only while its meter is present. A stale device the
    user deleted therefore stays deleted, because the tombstone of a removed
    device is the only memory of that decision which survives a reload. Its
    entities are re-created only once CKW reports the meter again.
    """
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    if data is None:
        return

    entities = [
        CkwSensor(coordinator, deliverypoint, meter, description)
        for deliverypoint, meter in _electric_meters(data).items()
        if deliverypoint in data.meters_present
        or not meter_device_was_removed(hass, entry.entry_id, deliverypoint)
        for description in SENSOR_DESCRIPTIONS
    ]
    if entities:
        async_add_entities(entities)

    @callback
    def _async_add_new_meters() -> None:
        """Give a delivery point that appears later the same entities (#134)."""
        current = coordinator.data
        if current is None:
            return
        registry = er.async_get(hass)
        new = [
            CkwSensor(coordinator, deliverypoint, meter, description)
            for deliverypoint, meter in _electric_meters(current).items()
            if deliverypoint in current.meters_present
            for description in SENSOR_DESCRIPTIONS
            if registry.async_get_entity_id(
                Platform.SENSOR,
                DOMAIN,
                meter_entity_unique_id(entry.entry_id, deliverypoint, description.key),
            )
            is None
        ]
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_meters))


def _electric_meters(data: CkwData) -> dict[str, CkwMeter | None]:
    """Every delivery point this entry knows, with its metadata.

    ``meters`` carries the accumulated totals and ``meter_info`` the metadata of
    the customer payload, so their union is the known meter set. Gas meters are
    dropped here, because they share the payload but are outside this product.
    """
    meters: dict[str, CkwMeter | None] = {}
    for deliverypoint in sorted(set(data.meter_info) | set(data.meters)):
        meter = data.meter_info.get(deliverypoint)
        if meter is not None and meter.fuel not in (None, Fuel.ELEC):
            continue
        meters[deliverypoint] = meter
    return meters


class CkwSensor(CkwEntity, SensorEntity):
    """One measurement of one delivery point, read from coordinator data."""

    entity_description: CkwSensorEntityDescription

    def __init__(
        self,
        coordinator: CkwCoordinator,
        deliverypoint: str,
        meter: CkwMeter | None,
        description: CkwSensorEntityDescription,
    ) -> None:
        """Bind the sensor to its meter and its description."""
        super().__init__(
            coordinator,
            deliverypoint=deliverypoint,
            key=description.key,
            meter=meter,
        )
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_suggested_display_precision = description.suggested_display_precision
        self._attr_entity_category = description.entity_category
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default

    @property
    def native_value(self) -> StateType | datetime:
        """The number, never an API call, from the last successful poll."""
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data, self.deliverypoint)

    @property
    def last_reset(self) -> datetime | None:
        """Start of the reported local day for the ``TOTAL`` yesterday sensor."""
        if self.entity_description.key != "consumption_yesterday":
            return None
        totals = _totals(self.coordinator.data, self.deliverypoint)
        if totals is None or totals.last_day_start is None:
            return None
        return totals.last_day_start.astimezone(TIME_ZONE)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Publication details on the total, interval details on the quarter hour."""
        data = self.coordinator.data
        if data is None:
            return None

        if self.entity_description.key == "consumption_total":
            totals = _totals(data, self.deliverypoint)
            if totals is None:
                return None
            attributes: dict[str, Any] = {
                "publication_delay_h": totals.publication_delay_h,
                "last_published_interval": totals.last_interval_end,
                "quality_intervals": totals.quality_intervals,
            }
            stale_since = self.stale_since
            if stale_since is not None:
                # Only set while the meter is stale, so a meter that comes back
                # loses the attribute again (#134).
                attributes["stale_since"] = stale_since
            return attributes

        if self.entity_description.key == "quarter_hour":
            reading = data.quarter_hour.get(self.deliverypoint)
            if reading is None:
                return None
            return {
                "interval_start": reading.interval_start,
                "interval_end": reading.interval_end,
                "publication_delay_h": reading.publication_delay_h,
            }

        return None
