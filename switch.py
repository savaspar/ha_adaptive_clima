from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OPT_AREAS, A_ID, A_NAME, A_INCLUDED
from .coordinator import HouseClimaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HouseClimaCoordinator = hass.data[DOMAIN][entry.entry_id]
    switches = []
    for area in entry.options.get(OPT_AREAS, []):
        aid = area.get(A_ID)
        name = area.get(A_NAME, aid)
        if aid:
            switches.append(IncludeAreaSwitch(coordinator, aid, name, bool(area.get(A_INCLUDED, True))))
    async_add_entities(switches, update_before_add=True)


class IncludeAreaSwitch(SwitchEntity):
    _attr_icon = "mdi:checkbox-marked-circle-outline"

    def __init__(self, coordinator: HouseClimaCoordinator, area_id: str, area_name: str, default_included: bool) -> None:
        self.coordinator = coordinator
        self.area_id = area_id
        self._attr_name = f"Include {area_name}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_include_{area_id}"
        self._attr_is_on = default_included
        self.coordinator.set_area_included(area_id, default_included)

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.coordinator.set_area_included(self.area_id, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.coordinator.set_area_included(self.area_id, False)
        self.async_write_ha_state()
