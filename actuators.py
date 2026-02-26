"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.


from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from homeassistant.core import HomeAssistant, State

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN


@dataclass
class ActuatorRead:
    setpoint: Optional[float] = None
    is_on: Optional[bool] = None
    hvac_modes: Optional[list[str]] = None
    hvac_mode: Optional[str] = None


class BaseActuator:
    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self.hass = hass
        self.entity_id = entity_id

    def _get_state(self) -> State | None:
        return self.hass.states.get(self.entity_id)


class ClimateActuator(BaseActuator):
    async def async_read(self) -> ActuatorRead:
        st = self._get_state()
        if not st:
            return ActuatorRead()
        temp = st.attributes.get("temperature")
        modes = st.attributes.get("hvac_modes") or []
        mode = st.state  # current hvac_mode is state string
        try:
            t = float(temp) if temp is not None else None
        except (TypeError, ValueError):
            t = None
        return ActuatorRead(setpoint=t, hvac_modes=list(modes), hvac_mode=str(mode))

    async def async_set_temperature(self, value: float) -> None:
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_temperature",
            {"entity_id": self.entity_id, "temperature": value},
            blocking=False,
        )

    async def async_set_hvac_mode(self, mode: str) -> None:
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_hvac_mode",
            {"entity_id": self.entity_id, "hvac_mode": mode},
            blocking=False,
        )


class NumberActuator(BaseActuator):
    async def async_read(self) -> ActuatorRead:
        st = self._get_state()
        if not st:
            return ActuatorRead()
        try:
            return ActuatorRead(setpoint=float(st.state))
        except (TypeError, ValueError):
            return ActuatorRead()

    async def async_set_value(self, value: float) -> None:
        await self.hass.services.async_call(
            NUMBER_DOMAIN,
            "set_value",
            {"entity_id": self.entity_id, "value": value},
            blocking=False,
        )


class SwitchActuator(BaseActuator):
    async def async_read(self) -> ActuatorRead:
        st = self._get_state()
        if not st:
            return ActuatorRead()
        return ActuatorRead(is_on=(st.state == "on"))

    async def async_turn_on(self) -> None:
        await self.hass.services.async_call(
            SWITCH_DOMAIN,
            "turn_on",
            {"entity_id": self.entity_id},
            blocking=False,
        )

    async def async_turn_off(self) -> None:
        await self.hass.services.async_call(
            SWITCH_DOMAIN,
            "turn_off",
            {"entity_id": self.entity_id},
            blocking=False,
        )
