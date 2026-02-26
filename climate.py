from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode, ClimateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HouseClimaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HouseClimaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AdaptiveClimaEntity(coordinator)], update_before_add=True)


class AdaptiveClimaEntity(ClimateEntity):
    _attr_name = "Adaptive Clima"
    _attr_temperature_unit = "°C"
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]

    def __init__(self, coordinator: HouseClimaCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_adaptive_clima"

    @property
    def current_temperature(self) -> Optional[float]:
        return self.coordinator.current_temperature

    @property
    def target_temperature(self) -> Optional[float]:
        return self.coordinator.house_target

    @property
    def hvac_mode(self) -> HVACMode:
        m = self.coordinator.hvac_mode
        if m == "heat":
            return HVACMode.HEAT
        if m == "cool":
            return HVACMode.COOL
        return HVACMode.OFF

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            return
        await self.coordinator.async_set_house_target(float(temp))
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            self.coordinator.set_hvac_mode("heat")
        elif hvac_mode == HVACMode.COOL:
            self.coordinator.set_hvac_mode("cool")
        else:
            self.coordinator.set_hvac_mode("off")
        self.async_write_ha_state()
