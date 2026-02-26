"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEFAULT_OFFSET, DEFAULT_DEFAULT_OFFSET
from .coordinator import HouseClimaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HouseClimaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoneOffsetNumber(coordinator)], update_before_add=True)


class ZoneOffsetNumber(NumberEntity):
    _attr_name = "Zone Warm Boost (°C)"
    _attr_native_unit_of_measurement = "°C"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 8.0
    _attr_native_step = 0.5
    _attr_mode = "slider"

    def __init__(self, coordinator: HouseClimaCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_zone_offset"

        default = float(coordinator.entry.options.get(CONF_DEFAULT_OFFSET, DEFAULT_DEFAULT_OFFSET))
        self._attr_native_value = default
        coordinator.set_zone_offset(default)

    async def async_set_native_value(self, value: float) -> None:
        value = float(value)
        self._attr_native_value = value
        self.coordinator.set_zone_offset(value)
        self.async_write_ha_state()
