"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

from typing import Any
from types import MappingProxyType
from collections.abc import Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    OPT_HOUSE_TARGET,
    OPT_ACTIVE_ZONE_ID,
    OPT_ACTIVE_ZONE_OFFSET,
    OPT_AREAS,
    A_ID,
)
from .coordinator import HouseClimaCoordinator

PLATFORMS = ["climate", "switch", "number"]


def _to_plain(obj: Any) -> Any:
    """Convert MappingProxyType / mappings into plain dicts recursively."""
    if isinstance(obj, MappingProxyType):
        obj = dict(obj)
    if isinstance(obj, Mapping):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_to_plain(v) for v in obj)
    return obj


def _strip_runtime(opts: dict) -> dict:
    """
    Options that should NOT trigger a reload:
    - house target temperature (thermostat)
    - active zone selection
    - active zone offset
    """
    x = _to_plain(opts or {})
    x.pop(OPT_HOUSE_TARGET, None)
    x.pop(OPT_ACTIVE_ZONE_ID, None)
    x.pop(OPT_ACTIVE_ZONE_OFFSET, None)
    return x


def _expected_include_unique_ids(entry: ConfigEntry) -> set[str]:
    out: set[str] = set()
    for area in (entry.options or {}).get(OPT_AREAS, []):
        area_id = area.get(A_ID) if hasattr(area, "get") else None
        if area_id:
            out.add(f"{entry.entry_id}_include_{area_id}")
    return out


async def _cleanup_stale_include_switches(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ent_reg = er.async_get(hass)
    expected = _expected_include_unique_ids(entry)

    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.domain != "switch":
            continue
        if ent.platform != DOMAIN:
            continue
        if not ent.unique_id or not ent.unique_id.startswith(f"{entry.entry_id}_include_"):
            continue
        if ent.unique_id not in expected:
            ent_reg.async_remove(ent.entity_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Reload platforms on structural options changes (areas/zones/globals) so entities are recreated.
    Do not reload for runtime changes (house_target / active zone / zone offset).
    Also remove orphan include switches when areas are removed.
    """
    domain_store = hass.data.setdefault(DOMAIN, {})
    store = domain_store.setdefault("_last_options", {})

    old = store.get(entry.entry_id, {})
    new = _to_plain(entry.options or {})

    old_stripped = _strip_runtime(old)
    new_stripped = _strip_runtime(new)

    store[entry.entry_id] = new

    if old_stripped == new_stripped:
        return

    await _cleanup_stale_include_switches(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = HouseClimaCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    hass.data[DOMAIN].setdefault("_last_options", {})[entry.entry_id] = _to_plain(entry.options or {})
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: HouseClimaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_unload()
        hass.data.get(DOMAIN, {}).get("_last_options", {}).pop(entry.entry_id, None)
    return ok
