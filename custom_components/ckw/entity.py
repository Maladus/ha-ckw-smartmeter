"""Entity base shared by every CKW platform.

A CKW entity belongs to exactly one delivery point, so the base class carries
the device identity and the append-only unique id of that meter (ADR-0003):

* the unique id is ``f"{entry_id}_{deliverypointnumber}_{key}"`` and is never
  recomputed or renamed, because statistics and history hang off it;
* the device is one per delivery point, named after the meter model plus the
  masked delivery point, and linked to the entry's service device via
  ``via_device``;
* availability follows the coordinator *and* the current poll: a meter that is
  known but no longer returned is unavailable rather than removed, so its
  entities and its history stay where they are (#134). Once the meter has been
  missing for longer than ``STALE_AFTER_DAYS`` the coordinator reports it in
  ``CkwData.stale_since``, which the consumption sensor exposes as
  ``stale_since``.

Entities only read ``coordinator.data``. They never call the API and never
perform I/O of their own (``docs/reference/conventions.md``).
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CkwMeter, mask_deliverypoint
from .const import DOMAIN, MANUFACTURER, MeterModel
from .coordinator import CkwCoordinator

__all__ = [
    "CkwDeviceInfo",
    "CkwEntity",
    "meter_device_identifier",
    "meter_device_was_removed",
    "meter_entity_unique_id",
]


def meter_entity_unique_id(entry_id: str, deliverypoint: str, key: str) -> str:
    """Return the append-only unique id of one entity of one meter (ADR-0003).

    The one definition of the scheme: the entity builds its own id from it, and
    the sensor platform asks it which entities a new meter still needs.
    """
    return f"{entry_id}_{deliverypoint}_{key}"


def meter_device_identifier(entry_id: str, deliverypoint: str) -> tuple[str, str]:
    """Return the device-registry identifier of one delivery point (ADR-0003).

    The masked delivery point is what the device is keyed by, so the identifier
    can be looked up without holding the full Zählpunkt (hard rule 3).
    """
    return (DOMAIN, f"{entry_id}_{mask_deliverypoint(deliverypoint)}")


def meter_device_was_removed(hass: HomeAssistant, entry_id: str, deliverypoint: str) -> bool:
    """Whether the user deleted this meter's device by hand (#134).

    Home Assistant keeps a tombstone for a device the user removed. That
    tombstone is the only memory of the decision which survives a reload, so the
    platform asks it before registering a device again. A delivery point the API
    no longer returns must stay deleted until the meter itself comes back.
    """
    return (
        dr.async_get(hass).deleted_devices.get_entry(
            {meter_device_identifier(entry_id, deliverypoint)}
        )
        is not None
    )


def CkwDeviceInfo(
    entry_id: str,
    deliverypoint: str,
    meter: CkwMeter | None,
) -> DeviceInfo:
    """Build the per-meter device, grouped under the entry's service device."""
    model = _meter_model(meter)
    masked = mask_deliverypoint(deliverypoint)
    return DeviceInfo(
        identifiers={meter_device_identifier(entry_id, deliverypoint)},
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
        self._attr_unique_id = meter_entity_unique_id(entry.entry_id, deliverypoint, key)
        self._attr_device_info = CkwDeviceInfo(entry.entry_id, deliverypoint, meter)

    @property
    def available(self) -> bool:
        """Whether the coordinator succeeded and this poll still reports the meter."""
        if not super().available:
            return False
        data = self.coordinator.data
        return data is not None and self.deliverypoint in data.meters_present

    @property
    def stale_since(self) -> datetime | None:
        """When the meter was first missed, once it has been missing long enough.

        ``None`` while the meter is present and while it is newly missing. A meter
        that CKW forgot for a poll or two is unavailable but not stale yet (#134).
        """
        data = self.coordinator.data
        if data is None:
            return None
        return data.stale_since.get(self.deliverypoint)


def _meter_model(meter: CkwMeter | None) -> str | None:
    """Return the meter model as the API sent it, for the device page."""
    if meter is None or meter.model is None:
        return None
    # An unknown enum value is kept as a raw string by ``api.py``.
    if isinstance(meter.model, MeterModel):
        return meter.model.value
    return str(meter.model)
