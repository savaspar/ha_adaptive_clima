"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

import logging
import uuid
from typing import Any
from types import MappingProxyType
from collections.abc import Mapping

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    # options
    OPT_AREAS,
    OPT_ZONES,
    OPT_SETPOINT_LIMIT,
    OPT_UNWIND_THRESHOLD,
    OPT_SCAN_INTERVAL,
    OPT_MIN_CHANGE_SECONDS,
    OPT_DEADBAND,
    OPT_DEFAULT_ZONE_OFFSET,
    DEFAULT_SETPOINT_LIMIT,
    DEFAULT_UNWIND_THRESHOLD,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_MIN_CHANGE_SECONDS,
    DEFAULT_DEADBAND,
    DEFAULT_DEFAULT_ZONE_OFFSET,
    # area keys
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
    # actuator types
    ACTUATOR_CLIMATE,
    ACTUATOR_NUMBER,
    ACTUATOR_SWITCH,
    # zone keys
    Z_ID,
    Z_AREA_IDS,
    Z_BUILTIN,
    Z_TIED_AREA_ID,
)

_LOGGER = logging.getLogger(__name__)


def _tkey(path: str) -> str:
    return f"component.{DOMAIN}.{path}"


def _to_plain(obj: Any) -> Any:
    """Convert MappingProxyType / mappings into plain dicts recursively (safe for HA options)."""
    if isinstance(obj, MappingProxyType):
        obj = dict(obj)
    if isinstance(obj, Mapping):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_to_plain(v) for v in obj)
    return obj


class AdaptiveClimaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        # Skip empty first form; go straight to onboarding.
        return await self.async_step_onboarding()

    async def async_step_onboarding(self, user_input: dict[str, Any] | None = None):
        schema = vol.Schema({vol.Required("ack", default=False): bool})
        if user_input:
            if not user_input.get("ack"):
                return self.async_show_form(step_id="onboarding", data_schema=schema, errors={"base": "must_ack"})
            return self.async_create_entry(title="Adaptive Clima", data={})
        return self.async_show_form(step_id="onboarding", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AdaptiveClimaOptionsFlow(config_entry)


class AdaptiveClimaOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._options = _to_plain(entry.options or {})
        self._options.setdefault(OPT_AREAS, [])
        self._options.setdefault(OPT_ZONES, [])

        self._pending_area: dict[str, Any] | None = None
        self._edit_area_id: str | None = None

        self._pending_zone_area_ids: list[str] | None = None
        self._edit_zone_id: str | None = None

    # ---------- translation helper ----------
    def _tr(self, path: str, fallback: str) -> str:
        key = _tkey(path)
        try:
            s = self.hass.helpers.translation.async_translate(key)
            return s if s != key else fallback
        except Exception:
            return fallback

    # ---------- registries ----------
    def _areas_registry(self):
        reg = ar.async_get(self.hass)
        areas = list(reg.async_list_areas())
        areas.sort(key=lambda a: (a.name or "").lower())
        return reg, areas

    def _configured_ha_area_ids(self) -> set[str]:
        return {a.get(A_HA_AREA_ID) for a in self._options.get(OPT_AREAS, []) if a.get(A_HA_AREA_ID)}

    def _entities_in_area_by_domain(self, ha_area_id: str, domain: str) -> list[str]:
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        out: list[str] = []
        for ent in ent_reg.entities.values():
            if ent.domain != domain:
                continue

            # Hide our own master climate entity
            if ent.entity_id == "climate.adaptive_clima":
                continue

            if ent.area_id == ha_area_id:
                out.append(ent.entity_id)
                continue

            if ent.device_id:
                dev = dev_reg.devices.get(ent.device_id)
                if dev and dev.area_id == ha_area_id:
                    out.append(ent.entity_id)

        out.sort()
        return out

    def _prefill_supports_from_climate(self, climate_entity_id: str) -> tuple[bool, bool]:
        st = self.hass.states.get(climate_entity_id)
        if not st:
            return True, True
        modes = st.attributes.get("hvac_modes") or []
        supports_heat = ("heat" in modes) or ("heat_cool" in modes)
        supports_cool = ("cool" in modes) or ("heat_cool" in modes)
        if not supports_heat and not supports_cool:
            return True, True
        return supports_heat, supports_cool

    # ---------- options helpers ----------
    def _area_id_to_name(self) -> dict[str, str]:
        """Return AdaptiveClima area_id -> current HA Area name (fallback to stored)."""
        # map ha_area_id -> name
        reg = ar.async_get(self.hass)
        ha_names: dict[str, str] = {a.id: a.name for a in reg.async_list_areas() if a.id and a.name}

        out: dict[str, str] = {}
        for a in self._options.get(OPT_AREAS, []):
            aid = a.get(A_ID)
            haid = a.get(A_HA_AREA_ID)
            stored = a.get(A_NAME)
            if aid:
                out[aid] = ha_names.get(haid) or stored or aid
        return out

    def _zone_key(self, area_ids: list[str]) -> tuple[str, ...]:
        return tuple(sorted(set(area_ids)))

    def _ensure_builtin_zones(self) -> None:
        """Ensure each area has a builtin zone; remove builtin zones for missing areas."""
        areas = self._options.get(OPT_AREAS, [])
        zones = list(self._options.get(OPT_ZONES, []))

        area_ids = {a.get(A_ID) for a in areas if a.get(A_ID)}
        existing_builtin = {z.get(Z_TIED_AREA_ID) for z in zones if z.get(Z_BUILTIN) and z.get(Z_TIED_AREA_ID)}

        # remove builtin zones whose tied area no longer exists
        zones = [z for z in zones if not (z.get(Z_BUILTIN) and z.get(Z_TIED_AREA_ID) and z.get(Z_TIED_AREA_ID) not in area_ids)]

        # add missing builtin zones
        for aid in sorted(area_ids):
            if aid not in existing_builtin:
                zones.append(
                    {
                        Z_ID: uuid.uuid4().hex[:8],
                        Z_AREA_IDS: [aid],
                        Z_BUILTIN: True,
                        Z_TIED_AREA_ID: aid,
                    }
                )

        self._options[OPT_ZONES] = zones

    def _zone_membership_custom(self, area_id: str) -> list[dict[str, Any]]:
        """Return custom zones that include this area_id."""
        out = []
        for z in self._options.get(OPT_ZONES, []):
            if z.get(Z_BUILTIN):
                continue
            if area_id in (z.get(Z_AREA_IDS) or []):
                out.append(z)
        return out

    def _zone_labels(self, include_builtin: bool) -> list[tuple[str, str]]:
        """
        Return list of (label, zone_id) for selection.
        Label is generated from current HA area names.
        """
        names = self._area_id_to_name()

        def label_for(z: dict[str, Any]) -> str:
            parts = [names.get(aid, aid) for aid in (z.get(Z_AREA_IDS) or [])]
            parts = sorted(parts, key=lambda s: s.lower())
            return "Warm Zone: " + "+".join(parts)

        items: list[tuple[str, str]] = []
        for z in self._options.get(OPT_ZONES, []):
            if not include_builtin and z.get(Z_BUILTIN):
                continue
            zid = z.get(Z_ID)
            if zid:
                items.append((label_for(z), zid))
        items.sort(key=lambda x: x[0].lower())
        return items

    def _format_areas_report(self) -> str:
        # translated labels for the report (already in en.json)
        t_unnamed = self._tr("options.report.unnamed", "Unnamed")
        t_none = self._tr("options.report.none_configured", "(no areas configured)")
        L_inc = self._tr("options.report.included", "Included (default)")
        L_temp = self._tr("options.report.temp_sensor", "Room temperature sensor")
        L_act = self._tr("options.report.actuator_entity", "Actuator entity")
        L_sh = self._tr("options.report.supports_heat", "Supports heating")
        L_sc = self._tr("options.report.supports_cool", "Supports cooling")
        L_min = self._tr("options.report.min_setpoint", "Min setpoint")
        L_max = self._tr("options.report.max_setpoint", "Max setpoint")
        L_step = self._tr("options.report.step", "Step")
        L_bias = self._tr("options.report.bias", "Bias")
        L_gain = self._tr("options.report.gain", "Gain")

        blocks = []
        for idx, a in enumerate(self._options.get(OPT_AREAS, []), start=1):
            name = a.get(A_NAME, t_unnamed)
            typ = a.get(A_ACTUATOR_TYPE, "?")
            header = f"[{idx}] {name} ({typ})"
            lines = [header, "-" * len(header)]

            def add(k: str, v: Any):
                lines.append(f"  {k}: {v}")

            add(L_inc, a.get(A_INCLUDED))
            add(L_temp, a.get(A_TEMP_SENSOR))
            add(L_act, a.get(A_ACTUATOR_ENTITY))
            add(L_sh, a.get(A_SUPPORTS_HEAT))
            add(L_sc, a.get(A_SUPPORTS_COOL))

            if typ in (ACTUATOR_CLIMATE, ACTUATOR_NUMBER):
                add(L_min, a.get(A_MIN_SETPOINT))
                add(L_max, a.get(A_MAX_SETPOINT))
                add(L_step, a.get(A_STEP))
                add(L_bias, a.get(A_BIAS))
                add(L_gain, a.get(A_GAIN))

            blocks.append("\n".join(lines))

        return "\n\n".join(blocks) if blocks else t_none

    def _format_zones_report(self) -> str:
        names = self._area_id_to_name()
        zones = [z for z in self._options.get(OPT_ZONES, []) if not z.get(Z_BUILTIN)]
        if not zones:
            return self._tr("options.zones.none_configured", "(no zones configured)")

        blocks = []
        idx = 0
        for z in zones:
            idx += 1
            zid = z.get(Z_ID, "?")
            builtin = bool(z.get(Z_BUILTIN))
            area_ids = list(z.get(Z_AREA_IDS) or [])
            parts = [names.get(aid, aid) for aid in area_ids]
            parts = sorted(parts, key=lambda s: s.lower())
            label = "Warm Zone: " + "+".join(parts) if parts else "Warm Zone: ?"
            header = f"[{idx}] {label}"
            lines = [header, "-" * len(header)]
            lines.append(f"  id: {zid}")
            lines.append(f"  areas: {', '.join(parts) if parts else '?'}")
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    def _actuator_type_options(self) -> list[dict[str, str]]:
        return [
            {"value": ACTUATOR_CLIMATE, "label": self._tr("options.actuator.climate", "Climate (HVAC)")},
            {"value": ACTUATOR_NUMBER, "label": self._tr("options.actuator.number", "Number (setpoint)")},
            {"value": ACTUATOR_SWITCH, "label": self._tr("options.actuator.switch", "Switch (on/off)")},
        ]

    # ---------- steps ----------
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        try:
            # Ensure builtin zones exist (safe, idempotent)
            self._ensure_builtin_zones()

            menu = [
                {"value": "globals", "label": self._tr("options.menu.globals", "Global Settings")},
                {"value": "areas", "label": self._tr("options.menu.areas", "Areas")},
                {"value": "add_area", "label": self._tr("options.menu.add_area", "Add Area")},
                {"value": "edit_area", "label": self._tr("options.menu.edit_area", "Edit Area")},
                {"value": "remove_area", "label": self._tr("options.menu.remove_area", "Remove Area")},
                {"value": "zones", "label": self._tr("options.menu.zones", "Zones")},
                {"value": "add_zone", "label": self._tr("options.menu.add_zone", "Add Custom Zone")},
                {"value": "edit_zone", "label": self._tr("options.menu.edit_zone", "Edit Custom Zone")},
                {"value": "remove_zone", "label": self._tr("options.menu.remove_zone", "Remove Custom Zone")},
                {"value": "zone_defaults", "label": self._tr("options.menu.zone_defaults", "Zone Defaults")},
            ]

            schema = vol.Schema(
                {
                    vol.Required("action", default="areas"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=menu, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )

            if user_input:
                act = user_input["action"]
                if act == "globals":
                    return await self.async_step_globals()
                if act == "areas":
                    return await self.async_step_areas()
                if act == "add_area":
                    return await self.async_step_add_area()
                if act == "edit_area":
                    return await self.async_step_pick_area_to_edit()
                if act == "remove_area":
                    return await self.async_step_remove_area()

                if act == "zones":
                    return await self.async_step_zones()
                if act == "add_zone":
                    return await self.async_step_add_zone_pick_areas()
                if act == "edit_zone":
                    return await self.async_step_pick_zone_to_edit()
                if act == "remove_zone":
                    return await self.async_step_remove_zone()
                if act == "zone_defaults":
                    return await self.async_step_zone_defaults()

            return self.async_show_form(step_id="init", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: init crashed")
            return self.async_show_form(step_id="init", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_globals(self, user_input: dict[str, Any] | None = None):
        try:
            schema = vol.Schema(
                {
                    vol.Required(
                        OPT_SETPOINT_LIMIT,
                        default=float(self._options.get(OPT_SETPOINT_LIMIT, DEFAULT_SETPOINT_LIMIT)),
                    ): vol.Coerce(float),
                    vol.Required(
                        OPT_UNWIND_THRESHOLD,
                        default=str(self._options.get(OPT_UNWIND_THRESHOLD, DEFAULT_UNWIND_THRESHOLD)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=["0.5", "1.0", "1.5", "2.0", "2.5", "3.0"], mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                    vol.Required(
                        OPT_DEADBAND,
                        default=float(self._options.get(OPT_DEADBAND, DEFAULT_DEADBAND)),
                    ): vol.Coerce(float),
                    vol.Required(
                        OPT_SCAN_INTERVAL,
                        default=int(self._options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                    ): int,
                    vol.Required(
                        OPT_MIN_CHANGE_SECONDS,
                        default=int(self._options.get(OPT_MIN_CHANGE_SECONDS, DEFAULT_MIN_CHANGE_SECONDS)),
                    ): int,
                }
            )

            if user_input:
                self._options[OPT_SETPOINT_LIMIT] = float(user_input[OPT_SETPOINT_LIMIT])
                self._options[OPT_UNWIND_THRESHOLD] = float(user_input[OPT_UNWIND_THRESHOLD])
                self._options[OPT_DEADBAND] = float(user_input[OPT_DEADBAND])
                self._options[OPT_SCAN_INTERVAL] = int(user_input[OPT_SCAN_INTERVAL])
                self._options[OPT_MIN_CHANGE_SECONDS] = int(user_input[OPT_MIN_CHANGE_SECONDS])
                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(step_id="globals", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: globals crashed")
            return self.async_show_form(step_id="globals", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_areas(self, user_input: dict[str, Any] | None = None):
        try:
            report = self._format_areas_report()
            schema = vol.Schema({})

            if user_input:
                return await self.async_step_init()

            return self.async_show_form(
                step_id="areas",
                data_schema=schema,
                description_placeholders={"report": report},
            )

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: areas crashed")
            return self.async_show_form(step_id="areas", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_zones(self, user_input: dict[str, Any] | None = None):
        try:
            report = self._format_zones_report()
            schema = vol.Schema({})

            if user_input:
                return await self.async_step_init()

            return self.async_show_form(
                step_id="zones",
                data_schema=schema,
                description_placeholders={"report": report},
            )

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: zones crashed")
            return self.async_show_form(step_id="zones", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_zone_defaults(self, user_input: dict[str, Any] | None = None):
        try:
            schema = vol.Schema(
                {
                    vol.Required(
                        OPT_DEFAULT_ZONE_OFFSET,
                        default=float(self._options.get(OPT_DEFAULT_ZONE_OFFSET, DEFAULT_DEFAULT_ZONE_OFFSET)),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.0, max=5.0, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C"
                        )
                    )
                }
            )
            if user_input:
                self._options[OPT_DEFAULT_ZONE_OFFSET] = float(user_input[OPT_DEFAULT_ZONE_OFFSET])
                return self.async_create_entry(title="", data=self._options)
            return self.async_show_form(step_id="zone_defaults", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: zone_defaults crashed")
            return self.async_show_form(step_id="zone_defaults", data_schema=vol.Schema({}), errors={"base": "unknown"})

    # ----- Add Area (3 steps) -----
    async def async_step_add_area(self, user_input: dict[str, Any] | None = None):
        try:
            _reg, areas = self._areas_registry()
            if not areas:
                return self.async_show_form(step_id="add_area", data_schema=vol.Schema({}), errors={"base": "no_areas"})

            area_names = [a.name for a in areas if a.name] or [a.id for a in areas]

            schema = vol.Schema(
                {
                    vol.Required("ha_area_name"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=area_names, mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                    vol.Required(A_TEMP_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["sensor"], multiple=False)
                    ),
                    vol.Required(A_ACTUATOR_TYPE, default=ACTUATOR_CLIMATE): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=self._actuator_type_options(), mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                }
            )

            if user_input:
                ha_area_name = user_input["ha_area_name"]
                area_obj = next((a for a in areas if a.name == ha_area_name), None) or next((a for a in areas if a.id == ha_area_name), None)
                if area_obj is None:
                    return self.async_show_form(step_id="add_area", data_schema=schema, errors={"base": "area_not_found"})

                if area_obj.id in self._configured_ha_area_ids():
                    return self.async_show_form(step_id="add_area", data_schema=schema, errors={"base": "already_configured"})

                self._pending_area = {
                    A_ID: uuid.uuid4().hex[:8],
                    A_HA_AREA_ID: area_obj.id,
                    A_NAME: area_obj.name,
                    A_TEMP_SENSOR: user_input[A_TEMP_SENSOR],
                    A_ACTUATOR_TYPE: user_input[A_ACTUATOR_TYPE],
                }
                return await self.async_step_add_area_actuator()

            return self.async_show_form(step_id="add_area", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: add_area step1 crashed")
            return self.async_show_form(step_id="add_area", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_add_area_actuator(self, user_input: dict[str, Any] | None = None):
        try:
            if not self._pending_area:
                return await self.async_step_add_area()

            typ = self._pending_area[A_ACTUATOR_TYPE]
            ha_area_id = self._pending_area[A_HA_AREA_ID]
            domain = "climate" if typ == ACTUATOR_CLIMATE else ("number" if typ == ACTUATOR_NUMBER else "switch")

            candidates = self._entities_in_area_by_domain(ha_area_id, domain)
            if not candidates:
                return self.async_show_form(step_id="add_area_actuator", data_schema=vol.Schema({}), errors={"base": "no_entities_in_area"})

            schema = vol.Schema(
                {
                    vol.Required(A_ACTUATOR_ENTITY): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=candidates, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )

            if user_input:
                self._pending_area[A_ACTUATOR_ENTITY] = user_input[A_ACTUATOR_ENTITY]
                return await self.async_step_add_area_config()

            return self.async_show_form(step_id="add_area_actuator", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: add_area step2 crashed")
            return self.async_show_form(step_id="add_area_actuator", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_add_area_config(self, user_input: dict[str, Any] | None = None):
        try:
            if not self._pending_area:
                return await self.async_step_add_area()

            typ = self._pending_area[A_ACTUATOR_TYPE]
            actuator_entity = self._pending_area[A_ACTUATOR_ENTITY]

            if typ == ACTUATOR_CLIMATE:
                pre_heat, pre_cool = self._prefill_supports_from_climate(actuator_entity)
            elif typ == ACTUATOR_SWITCH:
                pre_heat, pre_cool = True, False
            else:
                pre_heat, pre_cool = True, True

            if typ in (ACTUATOR_CLIMATE, ACTUATOR_NUMBER):
                schema = vol.Schema(
                    {
                        vol.Required(A_SUPPORTS_HEAT, default=pre_heat): bool,
                        vol.Required(A_SUPPORTS_COOL, default=pre_cool): bool,
                        vol.Required(A_MIN_SETPOINT, default=16.0): vol.Coerce(float),
                        vol.Required(A_MAX_SETPOINT, default=30.0): vol.Coerce(float),
                        vol.Required(A_STEP, default="0.5"): selector.SelectSelector(
                            selector.SelectSelectorConfig(options=["0.5", "1.0"], mode=selector.SelectSelectorMode.DROPDOWN)
                        ),
                        vol.Required(A_BIAS, default=0.0): vol.Coerce(float),
                        vol.Required(A_GAIN, default=1.0): vol.Coerce(float),
                        vol.Required(A_INCLUDED, default=True): bool,
                    }
                )
            else:
                schema = vol.Schema(
                    {
                        vol.Required(A_SUPPORTS_HEAT, default=pre_heat): bool,
                        vol.Required(A_SUPPORTS_COOL, default=pre_cool): bool,
                        vol.Required(A_INCLUDED, default=True): bool,
                    }
                )

            if user_input:
                sh = bool(user_input[A_SUPPORTS_HEAT])
                sc = bool(user_input[A_SUPPORTS_COOL])
                if not sh and not sc:
                    return self.async_show_form(step_id="add_area_config", data_schema=schema, errors={"base": "must_support_one"})

                self._pending_area[A_SUPPORTS_HEAT] = sh
                self._pending_area[A_SUPPORTS_COOL] = sc
                self._pending_area[A_INCLUDED] = bool(user_input[A_INCLUDED])

                if typ in (ACTUATOR_CLIMATE, ACTUATOR_NUMBER):
                    min_sp = float(user_input[A_MIN_SETPOINT])
                    max_sp = float(user_input[A_MAX_SETPOINT])
                    if min_sp > max_sp:
                        return self.async_show_form(step_id="add_area_config", data_schema=schema, errors={"base": "min_gt_max"})

                    self._pending_area[A_MIN_SETPOINT] = min_sp
                    self._pending_area[A_MAX_SETPOINT] = max_sp
                    self._pending_area[A_STEP] = float(user_input[A_STEP])
                    self._pending_area[A_BIAS] = float(user_input[A_BIAS])
                    self._pending_area[A_GAIN] = float(user_input[A_GAIN])

                new_list = list(self._options.get(OPT_AREAS, []))
                new_list.append(dict(self._pending_area))
                self._options[OPT_AREAS] = new_list
                self._pending_area = None

                # Ensure builtin zone exists for the new area
                self._ensure_builtin_zones()

                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(step_id="add_area_config", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: add_area step3 crashed")
            return self.async_show_form(step_id="add_area_config", data_schema=vol.Schema({}), errors={"base": "unknown"})

    # ----- Edit Area -----
    async def async_step_pick_area_to_edit(self, user_input: dict[str, Any] | None = None):
        try:
            m = {}
            for a in self._options.get(OPT_AREAS, []):
                aid = a.get(A_ID)
                name = a.get(A_NAME, aid)
                typ = a.get(A_ACTUATOR_TYPE, "?")
                if aid:
                    m[f"{name} ({typ})"] = aid

            if not m:
                return self.async_show_form(step_id="pick_area_to_edit", data_schema=vol.Schema({}), errors={"base": "no_areas_configured"})

            labels = sorted(m.keys(), key=lambda s: s.lower())
            schema = vol.Schema(
                {
                    vol.Required("area_label"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )

            if user_input:
                self._edit_area_id = m[user_input["area_label"]]
                return await self.async_step_edit_area()

            return self.async_show_form(step_id="pick_area_to_edit", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: pick_area_to_edit crashed")
            return self.async_show_form(step_id="pick_area_to_edit", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_edit_area(self, user_input: dict[str, Any] | None = None):
        try:
            if not self._edit_area_id:
                return await self.async_step_pick_area_to_edit()

            a = None
            for x in self._options.get(OPT_AREAS, []):
                if x.get(A_ID) == self._edit_area_id:
                    a = x
                    break
            if not a:
                return await self.async_step_pick_area_to_edit()

            typ = a.get(A_ACTUATOR_TYPE)

            if typ in (ACTUATOR_CLIMATE, ACTUATOR_NUMBER):
                schema = vol.Schema(
                    {
                        vol.Required(A_SUPPORTS_HEAT, default=bool(a.get(A_SUPPORTS_HEAT, False))): bool,
                        vol.Required(A_SUPPORTS_COOL, default=bool(a.get(A_SUPPORTS_COOL, False))): bool,
                        vol.Required(A_MIN_SETPOINT, default=float(a.get(A_MIN_SETPOINT, 16.0))): vol.Coerce(float),
                        vol.Required(A_MAX_SETPOINT, default=float(a.get(A_MAX_SETPOINT, 30.0))): vol.Coerce(float),
                        vol.Required(A_STEP, default=str(a.get(A_STEP, 0.5))): selector.SelectSelector(
                            selector.SelectSelectorConfig(options=["0.5", "1.0"], mode=selector.SelectSelectorMode.DROPDOWN)
                        ),
                        vol.Required(A_BIAS, default=float(a.get(A_BIAS, 0.0))): vol.Coerce(float),
                        vol.Required(A_GAIN, default=float(a.get(A_GAIN, 1.0))): vol.Coerce(float),
                        vol.Required(A_INCLUDED, default=bool(a.get(A_INCLUDED, True))): bool,
                    }
                )
            else:
                schema = vol.Schema(
                    {
                        vol.Required(A_SUPPORTS_HEAT, default=bool(a.get(A_SUPPORTS_HEAT, False))): bool,
                        vol.Required(A_SUPPORTS_COOL, default=bool(a.get(A_SUPPORTS_COOL, False))): bool,
                        vol.Required(A_INCLUDED, default=bool(a.get(A_INCLUDED, True))): bool,
                    }
                )

            if user_input:
                sh = bool(user_input[A_SUPPORTS_HEAT])
                sc = bool(user_input[A_SUPPORTS_COOL])
                if not sh and not sc:
                    return self.async_show_form(step_id="edit_area", data_schema=schema, errors={"base": "must_support_one"})

                a[A_SUPPORTS_HEAT] = sh
                a[A_SUPPORTS_COOL] = sc
                a[A_INCLUDED] = bool(user_input[A_INCLUDED])

                if typ in (ACTUATOR_CLIMATE, ACTUATOR_NUMBER):
                    min_sp = float(user_input[A_MIN_SETPOINT])
                    max_sp = float(user_input[A_MAX_SETPOINT])
                    if min_sp > max_sp:
                        return self.async_show_form(step_id="edit_area", data_schema=schema, errors={"base": "min_gt_max"})

                    a[A_MIN_SETPOINT] = min_sp
                    a[A_MAX_SETPOINT] = max_sp
                    a[A_STEP] = float(user_input[A_STEP])
                    a[A_BIAS] = float(user_input[A_BIAS])
                    a[A_GAIN] = float(user_input[A_GAIN])

                self._options[OPT_AREAS] = [x if x.get(A_ID) != self._edit_area_id else a for x in self._options.get(OPT_AREAS, [])]
                self._edit_area_id = None
                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(step_id="edit_area", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: edit_area crashed")
            return self.async_show_form(step_id="edit_area", data_schema=vol.Schema({}), errors={"base": "unknown"})

    # ----- Remove Area -----
    async def async_step_remove_area(self, user_input: dict[str, Any] | None = None):
        try:
            m = {}
            for a in self._options.get(OPT_AREAS, []):
                aid = a.get(A_ID)
                name = a.get(A_NAME, aid)
                typ = a.get(A_ACTUATOR_TYPE, "?")
                if aid:
                    m[f"{name} ({typ})"] = aid

            if not m:
                return self.async_show_form(step_id="remove_area", data_schema=vol.Schema({}), errors={"base": "no_areas_configured"})

            labels = sorted(m.keys(), key=lambda s: s.lower())
            schema = vol.Schema(
                {
                    vol.Required("area_label"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )

            if user_input:
                aid = m[user_input["area_label"]]

                # Block deletion if area is in any custom zone
                if self._zone_membership_custom(aid):
                    return self.async_show_form(step_id="remove_area", data_schema=schema, errors={"base": "area_in_custom_zone"})

                # remove area
                self._options[OPT_AREAS] = [a for a in self._options.get(OPT_AREAS, []) if a.get(A_ID) != aid]

                # remove its builtin zone
                self._options[OPT_ZONES] = [
                    z for z in self._options.get(OPT_ZONES, [])
                    if not (z.get(Z_BUILTIN) and z.get(Z_TIED_AREA_ID) == aid)
                ]

                # also ensure builtin zones are consistent
                self._ensure_builtin_zones()

                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(step_id="remove_area", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: remove_area crashed")
            return self.async_show_form(step_id="remove_area", data_schema=vol.Schema({}), errors={"base": "unknown"})

    # ----- Zones CRUD -----
    async def async_step_add_zone_pick_areas(self, user_input: dict[str, Any] | None = None):
        try:
            # only custom zones: 2..(x-1)
            area_names = self._area_id_to_name()
            area_ids = list(area_names.keys())
            if len(area_ids) < 3:
                # Need at least 3 areas to allow 2..x-1 without allowing all areas
                return self.async_show_form(step_id="add_zone_pick_areas", data_schema=vol.Schema({}), errors={"base": "need_more_areas_for_custom_zone"})

            opts = [{"value": aid, "label": area_names[aid]} for aid in sorted(area_ids, key=lambda k: area_names[k].lower())]
            schema = vol.Schema(
                {
                    vol.Optional("zone_area_ids", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=opts, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )

            if user_input:
                picked = user_input.get("zone_area_ids") or []
                if isinstance(picked, str):
                    picked = [picked]
                picked = list(picked)

                if len(picked) < 2:
                    return self.async_show_form(step_id="add_zone_pick_areas", data_schema=schema, errors={"base": "zone_need_two"})
                if len(picked) >= len(area_ids):
                    return self.async_show_form(step_id="add_zone_pick_areas", data_schema=schema, errors={"base": "zone_all_areas_forbidden"})

                key = self._zone_key(picked)
                for z in self._options.get(OPT_ZONES, []):
                    if self._zone_key(list(z.get(Z_AREA_IDS) or [])) == key:
                        return self.async_show_form(step_id="add_zone_pick_areas", data_schema=schema, errors={"base": "zone_duplicate"})

                self._pending_zone_area_ids = list(key)
                return await self.async_step_add_zone_confirm()

            return self.async_show_form(step_id="add_zone_pick_areas", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: add_zone_pick_areas crashed")
            return self.async_show_form(step_id="add_zone_pick_areas", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_add_zone_confirm(self, user_input: dict[str, Any] | None = None):
        try:
            if not self._pending_zone_area_ids:
                return await self.async_step_add_zone_pick_areas()

            names = self._area_id_to_name()
            prefix = self._tr("options.zones.warm_prefix", "Warm Zone: ")
            parts = sorted([names.get(aid, aid) for aid in self._pending_zone_area_ids], key=lambda s: s.lower())
            label = prefix + "+".join(parts)

            schema = vol.Schema({vol.Required("confirm", default=True): bool})

            if user_input is not None:
                zones = list(self._options.get(OPT_ZONES, []))
                zones.append(
                    {
                        Z_ID: uuid.uuid4().hex[:8],
                        Z_AREA_IDS: list(self._pending_zone_area_ids),
                        Z_BUILTIN: False,
                    }
                )
                self._options[OPT_ZONES] = zones
                self._pending_zone_area_ids = None
                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(
                step_id="add_zone_confirm",
                data_schema=schema,
                description_placeholders={"zone_label": label},
                last_step=True,
            )

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: add_zone_confirm crashed")
            return self.async_show_form(step_id="add_zone_confirm", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_pick_zone_to_edit(self, user_input: dict[str, Any] | None = None):
        try:
            items = self._zone_labels(include_builtin=False)
            if not items:
                return self.async_show_form(step_id="pick_zone_to_edit", data_schema=vol.Schema({}), errors={"base": "no_custom_zones"})

            labels = [lbl for (lbl, _zid) in items]
            map_lbl_to_id = {lbl: zid for (lbl, zid) in items}

            schema = vol.Schema(
                {
                    vol.Required("zone_label"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )
            if user_input:
                self._edit_zone_id = map_lbl_to_id[user_input["zone_label"]]
                return await self.async_step_edit_zone()

            return self.async_show_form(step_id="pick_zone_to_edit", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: pick_zone_to_edit crashed")
            return self.async_show_form(step_id="pick_zone_to_edit", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_edit_zone(self, user_input: dict[str, Any] | None = None):
        try:
            if not self._edit_zone_id:
                return await self.async_step_pick_zone_to_edit()

            zone = None
            for z in self._options.get(OPT_ZONES, []):
                if z.get(Z_ID) == self._edit_zone_id and not z.get(Z_BUILTIN):
                    zone = z
                    break
            if not zone:
                self._edit_zone_id = None
                return await self.async_step_pick_zone_to_edit()

            area_names = self._area_id_to_name()
            area_ids = list(area_names.keys())
            opts = [{"value": aid, "label": area_names[aid]} for aid in sorted(area_ids, key=lambda k: area_names[k].lower())]
            cur = list(zone.get(Z_AREA_IDS) or [])

            schema = vol.Schema(
                {
                    vol.Required("zone_area_ids", default=cur): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=opts, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )
            if user_input:
                picked = user_input.get("zone_area_ids") or []
                if isinstance(picked, str):
                    picked = [picked]
                picked = list(picked)

                if len(picked) < 2:
                    return self.async_show_form(step_id="edit_zone", data_schema=schema, errors={"base": "zone_need_two"})
                if len(picked) >= len(area_ids):
                    return self.async_show_form(step_id="edit_zone", data_schema=schema, errors={"base": "zone_all_areas_forbidden"})

                key = self._zone_key(picked)

                # uniqueness against all zones except itself
                for z in self._options.get(OPT_ZONES, []):
                    if z.get(Z_ID) == self._edit_zone_id:
                        continue
                    if self._zone_key(list(z.get(Z_AREA_IDS) or [])) == key:
                        return self.async_show_form(step_id="edit_zone", data_schema=schema, errors={"base": "zone_duplicate"})

                zone[Z_AREA_IDS] = list(key)

                # replace list object
                self._options[OPT_ZONES] = [z if z.get(Z_ID) != self._edit_zone_id else zone for z in self._options.get(OPT_ZONES, [])]
                self._edit_zone_id = None
                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(step_id="edit_zone", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: edit_zone crashed")
            return self.async_show_form(step_id="edit_zone", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_remove_zone(self, user_input: dict[str, Any] | None = None):
        try:
            items = self._zone_labels(include_builtin=False)
            if not items:
                return self.async_show_form(step_id="remove_zone", data_schema=vol.Schema({}), errors={"base": "no_custom_zones"})

            labels = [lbl for (lbl, _zid) in items]
            map_lbl_to_id = {lbl: zid for (lbl, zid) in items}

            schema = vol.Schema(
                {
                    vol.Required("zone_label"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            )
            if user_input:
                zid = map_lbl_to_id[user_input["zone_label"]]
                self._options[OPT_ZONES] = [z for z in self._options.get(OPT_ZONES, []) if z.get(Z_ID) != zid]
                return self.async_create_entry(title="", data=self._options)

            return self.async_show_form(step_id="remove_zone", data_schema=schema)

        except Exception:
            _LOGGER.exception("ADAPTIVE_CLIMA: remove_zone crashed")
            return self.async_show_form(step_id="remove_zone", data_schema=vol.Schema({}), errors={"base": "unknown"})
