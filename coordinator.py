from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    OPT_AREAS,
    OPT_HOUSE_TARGET,
    OPT_DEADBAND,
    OPT_SCAN_INTERVAL,
    OPT_MIN_CHANGE_SECONDS,
    OPT_SETPOINT_LIMIT,
    OPT_UNWIND_THRESHOLD,
    DEFAULT_HOUSE_TARGET,
    DEFAULT_DEADBAND,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_MIN_CHANGE_SECONDS,
    DEFAULT_SETPOINT_LIMIT,
    DEFAULT_UNWIND_THRESHOLD,
    A_ID,
    A_TEMP_SENSOR,
    A_ACTUATOR_TYPE,
    A_ACTUATOR_ENTITY,
    A_SUPPORTS_HEAT,
    A_SUPPORTS_COOL,
    A_MIN_SETPOINT,
    A_MAX_SETPOINT,
    A_STEP,
    A_BIAS,
    A_GAIN,
    A_INCLUDED,
    ACTUATOR_CLIMATE,
    ACTUATOR_NUMBER,
    ACTUATOR_SWITCH,
)

from .actuators import ClimateActuator, NumberActuator, SwitchActuator


@dataclass
class AreaRuntime:
    included: bool = True
    last_change: Optional[datetime] = None  # for setpoint/switch changes (NOT hvac_mode)


class HouseClimaCoordinator:
    """
    Global hvac_mode: off/heat/cool (single mode for all areas)

    Key behaviors:
    - Switching heat/cool forces all participating climate actuators immediately (independent of deadband/rate-limit)
    - Switching off turns ALL included devices off immediately (climate/switch)
    - Manual target change:
        - Immediately sync setpoint actuators to center = (target + bias) (NOT to band edges)
        - Switch actuators: immediate hysteresis based on new target
    - Background loop:
        - Uses per-area room sensor
        - Computes a bounded setpoint target within [center-limit, center+limit]
        - Far from target => aims toward band edge; near => unwinds back toward center
        - Nudges toward that target using step*gain, with per-area rate limiting
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self.hvac_mode: str = "off"  # "off"|"heat"|"cool"
        self.house_target: float = float(entry.options.get(OPT_HOUSE_TARGET, DEFAULT_HOUSE_TARGET))
        self.current_temperature: Optional[float] = None

        self.runtime: dict[str, AreaRuntime] = {}
        self._unsub_timer = None

        # remember last forced hvac_mode per climate entity to avoid spamming
        self._last_forced_mode: dict[str, str] = {}

        # prevent overlapping runs
        self._running = False

    # ---------- options getters ----------
    def options(self) -> dict[str, Any]:
        return dict(self.entry.options or {})

    def get_areas(self) -> list[dict[str, Any]]:
        return self.options().get(OPT_AREAS, [])

    def get_deadband(self) -> float:
        return float(self.options().get(OPT_DEADBAND, DEFAULT_DEADBAND))

    def get_scan_interval(self) -> int:
        return int(self.options().get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    def get_min_change_seconds(self) -> int:
        return int(self.options().get(OPT_MIN_CHANGE_SECONDS, DEFAULT_MIN_CHANGE_SECONDS))

    def get_setpoint_limit(self) -> float:
        return float(self.options().get(OPT_SETPOINT_LIMIT, DEFAULT_SETPOINT_LIMIT))

    def get_unwind_threshold(self) -> float:
        return float(self.options().get(OPT_UNWIND_THRESHOLD, DEFAULT_UNWIND_THRESHOLD))

    # ---------- lifecycle ----------
    async def async_setup(self) -> None:
        for a in self.get_areas():
            aid = a.get(A_ID)
            if aid and aid not in self.runtime:
                self.runtime[aid] = AreaRuntime(included=bool(a.get(A_INCLUDED, True)))

        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._async_tick,
            timedelta(seconds=self.get_scan_interval()),
        )
        await self._async_control_loop()

    async def async_unload(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    # ---------- external controls ----------
    @callback
    def set_area_included(self, area_id: str, included: bool) -> None:
        self.runtime.setdefault(area_id, AreaRuntime())
        self.runtime[area_id].included = included

    @callback
    def set_hvac_mode(self, mode: str) -> None:
        self.hvac_mode = mode
        if mode == "off":
            self.hass.async_create_task(self._async_turn_off_all_actuators())
        else:
            self.hass.async_create_task(self._async_control_loop())

    async def async_set_house_target(self, value: float) -> None:
        """Manual target change: persist, then immediate center-sync (target+bias)."""
        self.house_target = float(value)

        new_options = {**self.options(), OPT_HOUSE_TARGET: self.house_target}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)

        self.hass.async_create_task(self._async_apply_manual_target_center_sync())

    # ---------- helpers ----------
    def _read_float_state(self, entity_id: str) -> Optional[float]:
        st = self.hass.states.get(entity_id)
        if not st:
            return None
        try:
            return float(st.state)
        except (TypeError, ValueError):
            return None

    def _is_area_included(self, area: dict[str, Any]) -> bool:
        aid = area.get(A_ID)
        if not aid:
            return bool(area.get(A_INCLUDED, True))
        rt = self.runtime.get(aid)
        return rt.included if rt else bool(area.get(A_INCLUDED, True))

    def _area_supports_mode(self, area: dict[str, Any]) -> bool:
        if self.hvac_mode == "heat":
            return bool(area.get(A_SUPPORTS_HEAT, False))
        if self.hvac_mode == "cool":
            return bool(area.get(A_SUPPORTS_COOL, False))
        return False  # off

    def _rate_limited(self, area_id: str, min_change_seconds: int) -> bool:
        rt = self.runtime.setdefault(area_id, AreaRuntime())
        if rt.last_change is None:
            return False
        return (dt_util.utcnow() - rt.last_change).total_seconds() < min_change_seconds

    def _clamp(self, v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _round_to_step(self, v: float, step: float) -> float:
        if step <= 0:
            return v
        return round(round(v / step) * step, 2)

    async def _async_tick(self, _now: datetime) -> None:
        await self._async_control_loop()

    def _compute_banded_setpoint_target(
        self,
        room_temp: float,
        desired_room: float,
        center_sp: float,
        limit: float,
        unwind: float,
    ) -> float:
        """
        Returns S* within [center-limit, center+limit].

        Far (abs(e)>unwind): S* = center +/- limit
        Near (abs(e)<=unwind): S* = center +/- limit*(abs(e)/unwind)
        Direction depends on mode and whether room is above/below target.
        """
        e = room_temp - desired_room
        a = abs(e)

        if unwind <= 0:
            frac = 1.0
        else:
            frac = 1.0 if a >= unwind else (a / unwind)

        if self.hvac_mode == "cool":
            # too warm => lower setpoint, too cold => higher setpoint
            direction = -1.0 if e > 0 else 1.0
        else:
            # heat: too cold => higher setpoint, too warm => lower setpoint
            direction = 1.0 if e < 0 else -1.0

        return center_sp + direction * limit * frac

    # ---------- OFF behavior ----------
    async def _async_turn_off_all_actuators(self) -> None:
        """
        Turn off all included devices immediately (climate/switch).
        Number actuators have no on/off meaning -> ignored.
        """
        for area in self.get_areas():
            aid = area.get(A_ID)
            if not aid:
                continue
            if not self._is_area_included(area):
                continue

            typ = area.get(A_ACTUATOR_TYPE)
            ent = area.get(A_ACTUATOR_ENTITY)
            if not ent:
                continue

            if typ == ACTUATOR_CLIMATE:
                act = ClimateActuator(self.hass, ent)
                read = await act.async_read()
                if read.hvac_modes and "off" in read.hvac_modes:
                    await act.async_set_hvac_mode("off")
                self._last_forced_mode.pop(ent, None)

            elif typ == ACTUATOR_SWITCH:
                act = SwitchActuator(self.hass, ent)
                await act.async_turn_off()

        # Update UI temperature (average of all included sensors) after off
        temps: list[float] = []
        for a in self.get_areas():
            if not self._is_area_included(a):
                continue
            t = self._read_float_state(a.get(A_TEMP_SENSOR, ""))
            if t is not None:
                temps.append(t)
        self.current_temperature = (sum(temps) / len(temps)) if temps else None

    # ---------- core loop ----------
    async def _async_control_loop(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            # UI current_temperature: AVERAGE of participating areas in current mode
            temps: list[float] = []
            for a in self.get_areas():
                if not self._is_area_included(a):
                    continue
                if self.hvac_mode != "off" and not self._area_supports_mode(a):
                    continue
                t = self._read_float_state(a.get(A_TEMP_SENSOR, ""))
                if t is not None:
                    temps.append(t)
            self.current_temperature = (sum(temps) / len(temps)) if temps else None

            if self.hvac_mode == "off":
                return

            deadband = self.get_deadband()
            min_change_seconds = self.get_min_change_seconds()
            limit = max(0.0, self.get_setpoint_limit())
            unwind = max(0.0, self.get_unwind_threshold())
            desired = self.house_target

            for area in self.get_areas():
                aid = area.get(A_ID)
                if not aid:
                    continue
                if not self._is_area_included(area):
                    continue
                if not self._area_supports_mode(area):
                    continue

                typ = area.get(A_ACTUATOR_TYPE)

                # Always enforce climate mode immediately
                if typ == ACTUATOR_CLIMATE:
                    await self._ensure_climate_mode(area)

                room_temp = self._read_float_state(area.get(A_TEMP_SENSOR, ""))
                if room_temp is None:
                    continue

                # Rate limiting applies to setpoint/switch toggles, not to hvac_mode
                if self._rate_limited(aid, min_change_seconds):
                    continue

                if typ == ACTUATOR_SWITCH:
                    await self._handle_switch(area, room_temp, desired, deadband)
                    self.runtime[aid].last_change = dt_util.utcnow()
                    continue

                if typ == ACTUATOR_CLIMATE:
                    changed = await self._handle_climate_banded_nudge(area, room_temp, desired, deadband, limit, unwind)
                    if changed:
                        self.runtime[aid].last_change = dt_util.utcnow()
                    continue

                if typ == ACTUATOR_NUMBER:
                    changed = await self._handle_number_banded_nudge(area, room_temp, desired, deadband, limit, unwind)
                    if changed:
                        self.runtime[aid].last_change = dt_util.utcnow()
                    continue

        finally:
            self._running = False

    # ---------- manual target center sync ----------
    async def _async_apply_manual_target_center_sync(self) -> None:
        """
        Manual target change:
        - Immediately set setpoint actuators to center = (T + bias) (NOT to ±limit edges)
        - Switch actuators: immediate hysteresis decision
        """
        if self.hvac_mode == "off":
            return

        now = dt_util.utcnow()
        desired = self.house_target
        deadband = self.get_deadband()

        for area in self.get_areas():
            aid = area.get(A_ID)
            if not aid:
                continue
            if not self._is_area_included(area):
                continue
            if not self._area_supports_mode(area):
                continue

            typ = area.get(A_ACTUATOR_TYPE)

            # ensure climate mode
            if typ == ACTUATOR_CLIMATE:
                await self._ensure_climate_mode(area)

            room_temp = self._read_float_state(area.get(A_TEMP_SENSOR, ""))
            if room_temp is None:
                continue

            if typ == ACTUATOR_SWITCH:
                await self._handle_switch(area, room_temp, desired, deadband)
                self.runtime[aid].last_change = now
                continue

            # setpoint actuators: immediate center sync
            step = float(area.get(A_STEP, 0.5))
            lo = float(area.get(A_MIN_SETPOINT, 16.0))
            hi = float(area.get(A_MAX_SETPOINT, 30.0))
            bias = float(area.get(A_BIAS, 0.0))
            center = desired + bias
            target_sp = self._round_to_step(self._clamp(center, lo, hi), step)

            if typ == ACTUATOR_CLIMATE:
                act = ClimateActuator(self.hass, area[A_ACTUATOR_ENTITY])
                read = await act.async_read()
                if read.setpoint is None or abs(read.setpoint - target_sp) < 0.001:
                    continue
                await act.async_set_temperature(target_sp)
                self.runtime[aid].last_change = now
                continue

            if typ == ACTUATOR_NUMBER:
                act = NumberActuator(self.hass, area[A_ACTUATOR_ENTITY])
                read = await act.async_read()
                if read.setpoint is None or abs(read.setpoint - target_sp) < 0.001:
                    continue
                await act.async_set_value(target_sp)
                self.runtime[aid].last_change = now
                continue

        # fine-tune soon
        self.hass.async_create_task(self._async_control_loop())

    # ---------- actuator handlers ----------
    async def _ensure_climate_mode(self, area: dict[str, Any]) -> None:
        ent = area[A_ACTUATOR_ENTITY]
        act = ClimateActuator(self.hass, ent)
        read = await act.async_read()

        target_mode = self.hvac_mode
        hvac_modes = read.hvac_modes or []
        if target_mode not in hvac_modes:
            return

        last = self._last_forced_mode.get(ent)
        if last == target_mode:
            return

        await act.async_set_hvac_mode(target_mode)
        self._last_forced_mode[ent] = target_mode

    async def _handle_climate_banded_nudge(
        self,
        area: dict[str, Any],
        room_temp: float,
        desired_room: float,
        deadband: float,
        limit: float,
        unwind: float,
    ) -> bool:
        ent = area[A_ACTUATOR_ENTITY]
        act = ClimateActuator(self.hass, ent)
        read = await act.async_read()
        if read.setpoint is None:
            return False

        step = float(area.get(A_STEP, 0.5))
        lo = float(area.get(A_MIN_SETPOINT, 16.0))
        hi = float(area.get(A_MAX_SETPOINT, 30.0))
        bias = float(area.get(A_BIAS, 0.0))
        gain = float(area.get(A_GAIN, 1.0))

        center = desired_room + bias

        band_lo = self._clamp(center - limit, lo, hi)
        band_hi = self._clamp(center + limit, lo, hi)

        target_sp = self._compute_banded_setpoint_target(room_temp, desired_room, center, limit, unwind)
        target_sp = self._clamp(target_sp, band_lo, band_hi)
        target_sp = self._round_to_step(target_sp, step)

        if abs(read.setpoint - target_sp) < 0.001:
            return False

        delta = step * gain
        if read.setpoint < target_sp:
            new_sp = min(read.setpoint + delta, target_sp)
        else:
            new_sp = max(read.setpoint - delta, target_sp)

        new_sp = self._round_to_step(self._clamp(new_sp, lo, hi), step)

        # if room is within deadband and we’re at center, stop
        center_sp = self._round_to_step(self._clamp(center, lo, hi), step)
        if abs(room_temp - desired_room) <= deadband and abs(new_sp - center_sp) < 0.001:
            return False

        if abs(new_sp - read.setpoint) < 0.001:
            return False

        await act.async_set_temperature(new_sp)
        return True

    async def _handle_number_banded_nudge(
        self,
        area: dict[str, Any],
        room_temp: float,
        desired_room: float,
        deadband: float,
        limit: float,
        unwind: float,
    ) -> bool:
        ent = area[A_ACTUATOR_ENTITY]
        act = NumberActuator(self.hass, ent)
        read = await act.async_read()
        if read.setpoint is None:
            return False

        step = float(area.get(A_STEP, 0.5))
        lo = float(area.get(A_MIN_SETPOINT, 16.0))
        hi = float(area.get(A_MAX_SETPOINT, 30.0))
        bias = float(area.get(A_BIAS, 0.0))
        gain = float(area.get(A_GAIN, 1.0))

        center = desired_room + bias

        band_lo = self._clamp(center - limit, lo, hi)
        band_hi = self._clamp(center + limit, lo, hi)

        target_sp = self._compute_banded_setpoint_target(room_temp, desired_room, center, limit, unwind)
        target_sp = self._clamp(target_sp, band_lo, band_hi)
        target_sp = self._round_to_step(target_sp, step)

        if abs(read.setpoint - target_sp) < 0.001:
            return False

        delta = step * gain
        if read.setpoint < target_sp:
            new_sp = min(read.setpoint + delta, target_sp)
        else:
            new_sp = max(read.setpoint - delta, target_sp)

        new_sp = self._round_to_step(self._clamp(new_sp, lo, hi), step)

        center_sp = self._round_to_step(self._clamp(center, lo, hi), step)
        if abs(room_temp - desired_room) <= deadband and abs(new_sp - center_sp) < 0.001:
            return False

        if abs(new_sp - read.setpoint) < 0.001:
            return False

        await act.async_set_value(new_sp)
        return True

    async def _handle_switch(self, area: dict[str, Any], room_temp: float, desired: float, deadband: float) -> None:
        ent = area[A_ACTUATOR_ENTITY]
        act = SwitchActuator(self.hass, ent)
        read = await act.async_read()
        if read.is_on is None:
            return

        if self.hvac_mode == "heat":
            if room_temp < desired - deadband and not read.is_on:
                await act.async_turn_on()
            elif room_temp > desired + deadband and read.is_on:
                await act.async_turn_off()
        else:
            if room_temp > desired + deadband and not read.is_on:
                await act.async_turn_on()
            elif room_temp < desired - deadband and read.is_on:
                await act.async_turn_off()
