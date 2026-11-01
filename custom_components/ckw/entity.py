"""Entity base shared by every CKW platform.

A CKW entity belongs to exactly one delivery point, so the base class carries
the device identity and the append-only unique id of that meter (ADR-0003):

* the unique id is ``f"{entry_id}_{deliverypointnumber}_{key}"`` and is never
  recomputed or renamed, because statistics and history hang off it;
* the device is one per delivery point, named after the meter model plus the
  masked delivery point, and linked to the entry's service device via
  ``via_device``;
* availability follows the coordinator, and a meter that is missing from the
  coordinator's current data is unavailable rather than removed.

Entities only read ``coordinator.data``. They never call the API and never
perform I/O of their own (``docs/reference/conventions.md``).
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CkwMeter, mask_deliverypoint
from .const import DOMAIN, MANUFACTURER, MeterModel
from .coordinator import CkwCoordinator

__all__ = ["CkwDeviceInfo", "CkwEntity"]


def CkwDeviceInfo(
    entry_id: str,
    deliverypoint: str,
    meter: CkwMeter | None,
) -> DeviceInfo:
    """Build the per-meter device, grouped under the entry's service device."""
    model = _meter_model(meter)
    masked = mask_deliverypoint(deliverypoint)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{masked}")},
        via_device=(DOMAIN, entry_id),
        manufacturer=MANUFACTURER,
        model=model,
        name=f"{model or MANUFACTURER} {masked}",
    )


class CkwEntity(CoordinatorEntity[CkwCoordinator]):
    """One entity of one delivery point, reading only coordinator data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CkwCoordinator,
        *,
        deliverypoint: str,
        key: str,
        meter: CkwMeter | None = None,
    ) -> None:
        """Bind the entity to its meter and build its append-only unique id."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        if entry is None:
            raise ValueError("a CKW coordinator must belong to a config entry")
        self.deliverypoint = deliverypoint
        self.entity_key = key
        self.meter = meter
        self._attr_unique_id = f"{entry.entry_id}_{deliverypoint}_{key}"
        self._attr_device_info = CkwDeviceInfo(entry.entry_id, deliverypoint, meter)

    @property
    def available(self) -> bool:
        """Whether the coordinator succeeded and still reports this meter."""
        if not super().available:
            return False
        data = self.coordinator.data
        return data is not None and self.deliverypoint in data.meters


def _meter_model(meter: CkwMeter | None) -> str | None:
    """Return the meter model as the API sent it, for the device page."""
    if meter is None or meter.model is None:
        return None
    # An unknown enum value is kept as a raw string by ``api.py``.
    if isinstance(meter.model, MeterModel):
        return meter.model.value
    return str(meter.model)
