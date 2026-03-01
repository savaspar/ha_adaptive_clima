"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from .const import (
    OPT_AREAS,
    OPT_ZONES,
    OPT_HOUSE_TARGET,
    OPT_DEADBAND,
    OPT_SCAN_INTERVAL,
    OPT_MIN_CHANGE_SECONDS,
    OPT_SETPOINT_LIMIT,
    OPT_UNWIND_THRESHOLD,
    OPT_DEFAULT_ZONE_OFFSET,
    OPT_ACTIVE_ZONE_OFFSET,
    OPT_ACTIVE_ZONE_ID,
    DEFAULT_HOUSE_TARGET,
    DEFAULT_DEADBAND,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_MIN_CHANGE_SECONDS,
    DEFAULT_SETPOINT_LIMIT,
    DEFAULT_UNWIND_THRESHOLD,
    DEFAULT_DEFAULT_ZONE_OFFSET,
    DEFAULT_ACTIVE_ZONE_OFFSET,
    DEFAULT_ACTIVE_ZONE_ID,
    A_ID,
    A_HA_AREA_ID,
    A_NAME,
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
    Z_ID,
    Z_AREA_IDS,
)

from .actuators import ClimateActuator, NumberActuator, SwitchActuator


SUSPEND_ZONE_ID = "__suspend__"
OPT_LAST_NON_SUSPEND_ZONE_ID = "last_non_suspend_zone_id"


@dataclass
class AreaRuntime:
    included: bool = True
    last_change: Optional[datetime] = None  # rate-limit for setpoint changes (NOT hvac_mode)


class HouseClimaCoordinator:
    """
    Whole-house coordinator with Areas + Warm Zones.

    Correctness rules:
    - Global HVAC mode is one of: off / heat / cool.
    - When master thermostat is set to heat/cool, every included area that supports that mode
      is forced into that mode (even if the device was manually turned off).
    - Climate mode is re-verified continuously on every loop run.
    - AUTO mode is avoided (never chosen as a fallback).
    - Offset changes do NOT immediately rewrite devices; they apply on the next loop run.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self.hvac_mode: str = "off"  # "off"|"heat"|"cool"
        self.house_target: float = float(entry.options.get(OPT_HOUSE_TARGET, DEFAULT_HOUSE_TARGET))
        self.current_temperature: Optional[float] = None

        self.runtime: dict[str, AreaRuntime] = {}
        self._unsub_timer = None
        self._running = False
        self._listeners: set[Callable[[], None]] = set()

    # ---------- options ----------
    def options(self) -> dict[str, Any]:
        return dict(self.entry.options or {})

    def get_areas(self) -> list[dict[str, Any]]:
        return self.options().get(OPT_AREAS, [])

    def get_zones(self) -> list[dict[str, Any]]:
        return self.options().get(OPT_ZONES, [])

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

    def get_default_zone_offset(self) -> float:
        return float(self.options().get(OPT_DEFAULT_ZONE_OFFSET, DEFAULT_DEFAULT_ZONE_OFFSET))

    def get_active_zone_offset(self) -> float:
        return float(self.options().get(OPT_ACTIVE_ZONE_OFFSET, DEFAULT_ACTIVE_ZONE_OFFSET))

    def get_active_zone_id(self) -> Optional[str]:
        return self.options().get(OPT_ACTIVE_ZONE_ID, DEFAULT_ACTIVE_ZONE_ID)

    def _get_last_non_suspend_zone_id(self) -> Optional[str]:
        return self.options().get(OPT_LAST_NON_SUSPEND_ZONE_ID)

    def _set_last_non_suspend_zone_id(self, zid: Optional[str]) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.options(), OPT_LAST_NON_SUSPEND_ZONE_ID: zid},
        )

    def is_suspended(self) -> bool:
        return self.get_active_zone_id() == SUSPEND_ZONE_ID

    # ---------- lifecycle ----------
    async def async_setup(self) -> None:
        for a in self.get_areas():
            aid = a.get(A_ID)
            if aid and aid not in self.runtime:
                self.runtime[aid] = AreaRuntime(included=bool(a.get(A_INCLUDED, True)))

        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._async_tick,  # must exist
            timedelta(seconds=self.get_scan_interval()),
        )
        await self._async_control_loop()

    async def async_unload(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    async def async_restore_state(
        self,
        *,
        hvac_mode: Optional[str] = None,
        house_target: Optional[float] = None,
        preset_label: Optional[str] = None,
    ) -> None:
        """Restore runtime state after HA restart."""
        if house_target is not None:
            self.house_target = float(house_target)
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.options(), OPT_HOUSE_TARGET: self.house_target},
            )

        if preset_label is not None:
            if preset_label == "none":
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options={**self.options(), OPT_ACTIVE_ZONE_ID: None},
                )
            else:
                zid = self.get_preset_label_to_zone_id().get(preset_label)
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options={**self.options(), OPT_ACTIVE_ZONE_ID: zid},
                )

        if hvac_mode in ("off", "heat", "cool"):
            self.set_hvac_mode(hvac_mode)


    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a listener called when coordinator state should be refreshed."""
        self._listeners.add(listener)

        @callback
        def _unsub() -> None:
            self._listeners.discard(listener)

        return _unsub

    @callback
    def _notify_listeners(self) -> None:
        for lis in tuple(self._listeners):
            try:
                lis()
            except Exception:
                pass

    # ---------- external controls ----------
    @callback
    def set_area_included(self, area_id: str, included: bool) -> None:
        self.runtime.setdefault(area_id, AreaRuntime())
        self.runtime[area_id].included = included

    @callback
    def set_hvac_mode(self, mode: str) -> None:
        prev = self.hvac_mode
        self.hvac_mode = mode
        # If user enables heat/cool while suspended, exit Suspend and restore last zone (or none).
        if mode in ("heat", "cool") and self.get_active_zone_id() == SUSPEND_ZONE_ID:
            restore_zid = self._get_last_non_suspend_zone_id()
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.options(), OPT_ACTIVE_ZONE_ID: restore_zid},
            )
        self._notify_listeners()

        if mode == "off":
            self.hass.async_create_task(self._async_turn_off_all_actuators())
            return

        if prev != mode:
            for rt in self.runtime.values():
                rt.last_change = None

        # Force immediate apply; ensure correct start temp
        self.hass.async_create_task(self._async_apply_manual_target_center_sync(force_write_temp=True))

    async def async_set_house_target(self, value: float) -> None:
        self.house_target = float(value)
        self._notify_listeners()
        new_options = {**self.options(), OPT_HOUSE_TARGET: self.house_target}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        # If suspended or OFF, store the target but do not control devices.
        if self.is_suspended() or self.hvac_mode == "off":
            return
        self.hass.async_create_task(self._async_apply_manual_target_center_sync(force_write_temp=True))

    async def async_set_active_zone_offset(self, value: float) -> None:
        """Offset changes apply on next loop run (no immediate device rewrite)."""
        v = max(0.0, float(value))
        new_options = {**self.options(), OPT_ACTIVE_ZONE_OFFSET: v}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_listeners()

    async def async_set_active_zone(self, zone_id: Optional[str]) -> None:
        # Capture previous zone before updating options (needed to remember last preset when entering Suspend)
        prev_zone_id = self.get_active_zone_id()

        new_options = {**self.options(), OPT_ACTIVE_ZONE_ID: zone_id}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_listeners()

        # Maintain the "last non-suspend zone" pointer so Suspend exits predictably:
        # - Selecting none clears it
        # - Selecting any real zone sets it
        if zone_id is None:
            self._set_last_non_suspend_zone_id(None)
        elif zone_id != SUSPEND_ZONE_ID:
            self._set_last_non_suspend_zone_id(zone_id)

        # If entering Suspend, do not control actuators.
        if zone_id == SUSPEND_ZONE_ID:
            # If we entered suspend from a real zone, make sure it is remembered
            if prev_zone_id and prev_zone_id != SUSPEND_ZONE_ID and prev_zone_id is not None:
                self._set_last_non_suspend_zone_id(prev_zone_id)
            return

        # Zone selection should apply immediately (like manual change)
        self.hass.async_create_task(self._async_apply_manual_target_center_sync(force_write_temp=True))

    async def async_set_active_zone_by_preset_label(self, label: str) -> None:
        if label == "none":
            await self.async_set_active_zone(None)
            return
        if label == "Suspend":
            await self.async_set_active_zone(SUSPEND_ZONE_ID)
            return
        mapping = self.get_preset_label_to_zone_id()
        await self.async_set_active_zone(mapping.get(label))

    # ---------- preset labels ----------
    def _area_id_to_ha_area_id(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for a in self.get_areas():
            aid = a.get(A_ID)
            haid = a.get(A_HA_AREA_ID)
            if aid and haid:
                out[aid] = haid
        return out

    def _ha_area_id_to_name(self) -> dict[str, str]:
        reg = ar.async_get(self.hass)
        return {a.id: a.name for a in reg.async_list_areas() if a.id and a.name}

    def _area_display_name(self, area_id: str) -> str:
        aid_to_haid = self._area_id_to_ha_area_id()
        haid = aid_to_haid.get(area_id)
        if haid:
            name = self._ha_area_id_to_name().get(haid)
            if name:
                return name
        for a in self.get_areas():
            if a.get(A_ID) == area_id:
                return a.get(A_NAME) or area_id
        return area_id

    def _zone_preset_label(self, zone: dict[str, Any]) -> str:
        area_ids = list(zone.get(Z_AREA_IDS) or [])
        parts = sorted([self._area_display_name(aid) for aid in area_ids], key=lambda s: s.lower())
        joined = "+".join(parts) if parts else "?"
        return f"Warm Zone: {joined}"

    def get_preset_label_to_zone_id(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for z in self.get_zones():
            zid = z.get(Z_ID)
            if zid:
                out.setdefault(self._zone_preset_label(z), zid)
        return out

    def get_preset_labels(self) -> list[str]:
        labels = list(self.get_preset_label_to_zone_id().keys())
        labels.sort(key=lambda s: s.lower())
        return labels

    def get_active_preset_label(self) -> Optional[str]:
        zid = self.get_active_zone_id()
        if zid == SUSPEND_ZONE_ID:
            return "Suspend"
        if not zid:
            return None
        for z in self.get_zones():
            if z.get(Z_ID) == zid:
                return self._zone_preset_label(z)
        return None

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
        return False

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

    def _area_in_active_zone(self, area_id: str) -> bool:
        zid = self.get_active_zone_id()
        if not zid or zid == SUSPEND_ZONE_ID:
            return False
        for z in self.get_zones():
            if z.get(Z_ID) == zid:
                return area_id in (z.get(Z_AREA_IDS) or [])
        return False

    def _desired_room_for_area(self, area_id: str) -> float:
        base = self.house_target
        if self._area_in_active_zone(area_id):
            base += float(self.get_active_zone_offset())
        return base

    async def _async_tick(self, _now: datetime) -> None:
        """Timer callback for control loop."""
        await self._async_control_loop()

    def _compute_banded_setpoint_target(self, room_temp: float, desired_room: float, center_sp: float, limit: float, unwind: float) -> float:
        e = room_temp - desired_room
        a = abs(e)
        frac = 1.0 if unwind <= 0 else (1.0 if a >= unwind else (a / unwind))
        if self.hvac_mode == "cool":
            direction = -1.0 if e > 0 else 1.0
        else:
            direction = 1.0 if e < 0 else -1.0
        return center_sp + direction * limit * frac

    # ---------- OFF behavior ----------
    async def _async_auto_suspend_if_any_actuator_on(self) -> None:
        """If thermostat is OFF and any included actuator is ON, enter Suspend."""
        if self.get_active_zone_id() == SUSPEND_ZONE_ID:
            return
        for area in self.get_areas():
            aid = area.get(A_ID)
            if not aid or not self._is_area_included(area):
                continue
            typ = area.get(A_ACTUATOR_TYPE)
            ent = area.get(A_ACTUATOR_ENTITY)
            if not ent:
                continue
            if typ == ACTUATOR_SWITCH:
                sw = SwitchActuator(self.hass, ent)
                read = await sw.async_read()
                if read.is_on:
                    await self.async_set_active_zone(SUSPEND_ZONE_ID)
                    return
            if typ == ACTUATOR_CLIMATE:
                ca = ClimateActuator(self.hass, ent)
                read = await ca.async_read()
                if read.hvac_mode and read.hvac_mode != "off":
                    await self.async_set_active_zone(SUSPEND_ZONE_ID)
                    return
        # number actuator has no on/off notion; ignore

    async def _async_turn_off_all_actuators(self) -> None:
        for area in self.get_areas():
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
            elif typ == ACTUATOR_SWITCH:
                await SwitchActuator(self.hass, ent).async_turn_off()

        temps: list[float] = []
        for a in self.get_areas():
            if self._is_area_included(a):
                t = self._read_float_state(a.get(A_TEMP_SENSOR, ""))
                if t is not None:
                    temps.append(t)
        self.current_temperature = (sum(temps) / len(temps)) if temps else None
        self._notify_listeners()

    # ---------- mode forcing ----------
    def _select_supported_hvac_mode(self, hvac_modes: list[str] | None, desired: str) -> Optional[str]:
        """Avoid AUTO. Prefer heat/cool; else heat_cool; else None."""
        modes = hvac_modes or []
        if desired in modes:
            return desired
        if desired in ("heat", "cool") and "heat_cool" in modes:
            return "heat_cool"
        return None

    async def _force_climate_mode(self, entity_id: str) -> None:
        act = ClimateActuator(self.hass, entity_id)
        read = await act.async_read()
        target = self._select_supported_hvac_mode(read.hvac_modes, self.hvac_mode)
        if not target:
            return
        if read.hvac_mode != target:
            await act.async_set_hvac_mode(target)

    # ---------- core loop ----------
    async def _async_control_loop(self) -> None:
        if self._running:
            return
        self._running = True
        try:
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
            self._notify_listeners()

            if self.hvac_mode == "off":
                # In OFF, we do not control actuators, but we still show average temperature.
                # If any included actuator is found ON, automatically go to Suspend.
                await self._async_auto_suspend_if_any_actuator_on()
                return

            if self.is_suspended():
                # Suspend: show average temperature but do not control actuators.
                return

            deadband = self.get_deadband()
            min_change_seconds = self.get_min_change_seconds()
            limit = max(0.0, self.get_setpoint_limit())
            unwind = max(0.0, self.get_unwind_threshold())

            for area in self.get_areas():
                aid = area.get(A_ID)
                if not aid or not self._is_area_included(area) or not self._area_supports_mode(area):
                    continue

                typ = area.get(A_ACTUATOR_TYPE)
                ent = area.get(A_ACTUATOR_ENTITY)

                if typ == ACTUATOR_CLIMATE and ent:
                    await self._force_climate_mode(ent)

                room_temp = self._read_float_state(area.get(A_TEMP_SENSOR, ""))
                if room_temp is None:
                    continue

                desired = self._desired_room_for_area(aid)

                if typ == ACTUATOR_SWITCH:
                    await self._handle_switch(area, room_temp, desired, deadband)
                    continue

                if self._rate_limited(aid, min_change_seconds):
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

    # ---------- immediate apply ----------
    async def _async_apply_manual_target_center_sync(self, force_write_temp: bool = False) -> None:
        if self.hvac_mode == "off":
            return

        now = dt_util.utcnow()
        deadband = self.get_deadband()

        for area in self.get_areas():
            aid = area.get(A_ID)
            if not aid or not self._is_area_included(area) or not self._area_supports_mode(area):
                continue

            typ = area.get(A_ACTUATOR_TYPE)
            ent = area.get(A_ACTUATOR_ENTITY)
            if not ent:
                continue

            room_temp = self._read_float_state(area.get(A_TEMP_SENSOR, ""))
            if room_temp is None:
                continue

            desired = self._desired_room_for_area(aid)

            if typ == ACTUATOR_SWITCH:
                await self._handle_switch(area, room_temp, desired, deadband)
                continue

            step = float(area.get(A_STEP, 0.5))
            lo = float(area.get(A_MIN_SETPOINT, 16.0))
            hi = float(area.get(A_MAX_SETPOINT, 30.0))
            bias = float(area.get(A_BIAS, 0.0))
            center = desired + bias
            target_sp = self._round_to_step(self._clamp(center, lo, hi), step)

            if typ == ACTUATOR_CLIMATE:
                await self._force_climate_mode(ent)
                act = ClimateActuator(self.hass, ent)
                read = await act.async_read()
                if force_write_temp or read.setpoint is None or abs(read.setpoint - target_sp) >= 0.001:
                    await act.async_set_temperature(target_sp)
                    self.runtime[aid].last_change = now
                continue

            if typ == ACTUATOR_NUMBER:
                act = NumberActuator(self.hass, ent)
                read = await act.async_read()
                if force_write_temp or read.setpoint is None or abs(read.setpoint - target_sp) >= 0.001:
                    await act.async_set_value(target_sp)
                    self.runtime[aid].last_change = now
                continue

        self.hass.async_create_task(self._async_control_loop())

    # ---------- actuator handlers ----------
    async def _handle_climate_banded_nudge(self, area: dict[str, Any], room_temp: float, desired_room: float, deadband: float, limit: float, unwind: float) -> bool:
        ent = area[A_ACTUATOR_ENTITY]
        act = ClimateActuator(self.hass, ent)
        read = await act.async_read()

        step = float(area.get(A_STEP, 0.5))
        lo = float(area.get(A_MIN_SETPOINT, 16.0))
        hi = float(area.get(A_MAX_SETPOINT, 30.0))
        bias = float(area.get(A_BIAS, 0.0))
        gain = float(area.get(A_GAIN, 1.0))
        center = desired_room + bias

        if read.setpoint is None:
            center_sp = self._round_to_step(self._clamp(center, lo, hi), step)
            await act.async_set_temperature(center_sp)
            return True

        band_lo = self._clamp(center - limit, lo, hi)
        band_hi = self._clamp(center + limit, lo, hi)

        target_sp = self._compute_banded_setpoint_target(room_temp, desired_room, center, limit, unwind)
        target_sp = self._clamp(target_sp, band_lo, band_hi)
        target_sp = self._round_to_step(target_sp, step)

        if abs(read.setpoint - target_sp) < 0.001:
            return False

        delta = step * gain
        new_sp = min(read.setpoint + delta, target_sp) if read.setpoint < target_sp else max(read.setpoint - delta, target_sp)
        new_sp = self._round_to_step(self._clamp(new_sp, lo, hi), step)

        center_sp = self._round_to_step(self._clamp(center, lo, hi), step)
        if abs(room_temp - desired_room) <= deadband and abs(new_sp - center_sp) < 0.001:
            return False
        if abs(new_sp - read.setpoint) < 0.001:
            return False

        await act.async_set_temperature(new_sp)
        return True

    async def _handle_number_banded_nudge(self, area: dict[str, Any], room_temp: float, desired_room: float, deadband: float, limit: float, unwind: float) -> bool:
        ent = area[A_ACTUATOR_ENTITY]
        act = NumberActuator(self.hass, ent)
        read = await act.async_read()

        step = float(area.get(A_STEP, 0.5))
        lo = float(area.get(A_MIN_SETPOINT, 16.0))
        hi = float(area.get(A_MAX_SETPOINT, 30.0))
        bias = float(area.get(A_BIAS, 0.0))
        gain = float(area.get(A_GAIN, 1.0))
        center = desired_room + bias

        if read.setpoint is None:
            center_sp = self._round_to_step(self._clamp(center, lo, hi), step)
            await act.async_set_value(center_sp)
            return True

        band_lo = self._clamp(center - limit, lo, hi)
        band_hi = self._clamp(center + limit, lo, hi)

        target_sp = self._compute_banded_setpoint_target(room_temp, desired_room, center, limit, unwind)
        target_sp = self._clamp(target_sp, band_lo, band_hi)
        target_sp = self._round_to_step(target_sp, step)

        if abs(read.setpoint - target_sp) < 0.001:
            return False

        delta = step * gain
        new_sp = min(read.setpoint + delta, target_sp) if read.setpoint < target_sp else max(read.setpoint - delta, target_sp)
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
