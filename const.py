"""Adaptive Clima - Integration in Home-Assistant - Whole-House Adaptive Thermostat with Zones"""

# Copyright (c) 2026 Primeraid Europe (Private Capital Company – IKE)
# Licensed under the Adaptive Clima License (Source-Available, No Redistribution).
# See LICENSE in the project root for full license text.

from __future__ import annotations

DOMAIN = "adaptive_clima"

# Options keys
OPT_AREAS = "areas"
OPT_ZONES = "zones"

OPT_HOUSE_TARGET = "house_target"
OPT_DEADBAND = "deadband"
OPT_SCAN_INTERVAL = "scan_interval_seconds"
OPT_MIN_CHANGE_SECONDS = "min_change_seconds"
OPT_SETPOINT_LIMIT = "setpoint_limit"
OPT_UNWIND_THRESHOLD = "unwind_threshold"

OPT_DEFAULT_ZONE_OFFSET = "default_zone_offset"
OPT_ACTIVE_ZONE_OFFSET = "active_zone_offset"
OPT_ACTIVE_ZONE_ID = "active_zone_id"

DEFAULT_HOUSE_TARGET = 21.0

# Defaults (testing defaults)
DEFAULT_DEADBAND = 0.5
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_MIN_CHANGE_SECONDS = 60

DEFAULT_SETPOINT_LIMIT = 3.0
DEFAULT_UNWIND_THRESHOLD = 2.0

DEFAULT_DEFAULT_ZONE_OFFSET = 3.0
DEFAULT_ACTIVE_ZONE_OFFSET = 3.0
DEFAULT_ACTIVE_ZONE_ID = None

# Area dict keys
A_ID = "id"
A_HA_AREA_ID = "ha_area_id"
A_NAME = "name"
A_TEMP_SENSOR = "temp_sensor"
A_ACTUATOR_TYPE = "actuator_type"          # "climate" | "number" | "switch"
A_ACTUATOR_ENTITY = "actuator_entity"

A_SUPPORTS_HEAT = "supports_heat"
A_SUPPORTS_COOL = "supports_cool"

A_MIN_SETPOINT = "min_setpoint"
A_MAX_SETPOINT = "max_setpoint"
A_STEP = "step"
A_BIAS = "bias"
A_GAIN = "gain"

A_INCLUDED = "included"

ACTUATOR_CLIMATE = "climate"
ACTUATOR_NUMBER = "number"
ACTUATOR_SWITCH = "switch"

# Zone dict keys (stored in OPT_ZONES)
Z_ID = "id"
Z_AREA_IDS = "area_ids"
Z_BUILTIN = "builtin"            # True for per-area zones, False for custom zones
Z_TIED_AREA_ID = "tied_area_id"  # set only for builtin zones
