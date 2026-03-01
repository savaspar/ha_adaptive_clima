"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

from typing import Optional

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HouseClimaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HouseClimaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoneOffsetNumber(coordinator)], update_before_add=True)


class ZoneOffsetNumber(NumberEntity):
    _attr_name = "Zone Offset"
    _attr_icon = "mdi:thermometer-plus"

    # Slider behavior + range
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0.0
    _attr_native_max_value = 8.0
    _attr_native_step = 0.5

    def __init__(self, coordinator: HouseClimaCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_zone_offset"

    @property
    def native_unit_of_measurement(self) -> str:
        # Follow Home Assistant unit system (°C/°F) for display.
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> Optional[float]:
        return float(self.coordinator.get_active_zone_offset())

    async def async_set_native_value(self, value: float) -> None:
        # Value is provided by HA in the user's configured unit system.
        await self.coordinator.async_set_active_zone_offset(float(value))
        self.async_write_ha_state()
