"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode, ClimateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import HouseClimaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HouseClimaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AdaptiveClimaEntity(coordinator)], update_before_add=True)


class AdaptiveClimaEntity(RestoreEntity, ClimateEntity):
    _attr_name = "Adaptive Clima"
    _attr_icon = "mdi:home-thermometer"
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]

    def __init__(self, coordinator: HouseClimaCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_adaptive_clima"

    def _is_suspended(self) -> bool:
        return self.coordinator.is_suspended()

    @property
    def temperature_unit(self) -> str:
        # Follow Home Assistant unit system (°C/°F).
        return self.hass.config.units.temperature_unit

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_coordinator = self.coordinator.async_add_listener(self.async_write_ha_state)

        last = await self.async_get_last_state()
        if not last:
            return

        # Restore hvac mode
        st = (last.state or "").lower()
        hvac_mode: Optional[str] = None
        if st in ("off", "heat", "cool"):
            hvac_mode = st

        # Restore target temp (HA stores it in attributes['temperature'])
        house_target: Optional[float] = None
        try:
            if "temperature" in last.attributes and last.attributes["temperature"] is not None:
                house_target = float(last.attributes["temperature"])
        except (TypeError, ValueError):
            house_target = None

        # Restore preset (warm zone)
        preset_label: Optional[str] = None
        try:
            pm = last.attributes.get("preset_mode")
            if isinstance(pm, str):
                preset_label = pm
        except Exception:
            preset_label = None

        # Apply restore into coordinator
        await self.coordinator.async_restore_state(
            hvac_mode=hvac_mode,
            house_target=house_target,
            preset_label=preset_label,
        )

        self.async_write_ha_state()

    @property
    def current_temperature(self) -> Optional[float]:
        return self.coordinator.current_temperature

    @property
    def target_temperature(self) -> Optional[float]:
        return self.coordinator.house_target

    @property
    def hvac_mode(self) -> HVACMode:
        if self._is_suspended():
            return HVACMode.OFF
        m = self.coordinator.hvac_mode
        if m == "heat":
            return HVACMode.HEAT
        if m == "cool":
            return HVACMode.COOL
        return HVACMode.OFF

    @property
    def preset_modes(self) -> list[str]:
        return ["none", "Suspend"] + self.coordinator.get_preset_labels()

    @property
    def preset_mode(self) -> Optional[str]:
        return self.coordinator.get_active_preset_label() or "none"

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # While suspended, preset changes are ignored until user explicitly selects HEAT/COOL.
        if self._is_suspended() and preset_mode != "Suspend":
            self.async_write_ha_state()
            return
        await self.coordinator.async_set_active_zone_by_preset_label(preset_mode)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            return
        await self.coordinator.async_set_house_target(float(temp))
        self.async_write_ha_state()


    async def async_will_remove_from_hass(self) -> None:
        if getattr(self, "_unsub_coordinator", None):
            self._unsub_coordinator()
            self._unsub_coordinator = None
        await super().async_will_remove_from_hass()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # If suspended, OFF should keep Suspend (no device shutdown). Heat/Cool exits Suspend.
        if self._is_suspended() and hvac_mode == HVACMode.OFF:
            self.async_write_ha_state()
            return
        if hvac_mode == HVACMode.HEAT:
            self.coordinator.set_hvac_mode("heat")
        elif hvac_mode == HVACMode.COOL:
            self.coordinator.set_hvac_mode("cool")
        else:
            self.coordinator.set_hvac_mode("off")
        self.async_write_ha_state()
