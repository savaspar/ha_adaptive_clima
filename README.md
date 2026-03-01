# ❄️ Adaptive Clima: Multi-Area Adaptive Thermostat with Zones 🌡️


[Adaptive Clima](https://github.com/savaspar/ha_adaptive_clima) is a custom component for [Home Assistant](https://www.home-assistant.io/) that helps managing, configuring, fine-tune and controlling the various heating and cooling sources of a multi-area environment with one single thermostat, using smart algorithms and strong sensing logic.
<br><br>
<strong>Coordinate multiple area devices using your preferred area sensors as “truth”, and **boost** selected areas and Custom Zones.</strong><br/>
<img src="https://raw.githubusercontent.com/savaspar/ha_adaptive_clima/refs/heads/main/brand/logo.png" alt="logo" width="200px" height="200px" />
<p>
  <img alt="Integration" src="https://img.shields.io/badge/type-integration-blue" />
  <img alt="UI Config" src="https://img.shields.io/badge/config-UI%20only-brightgreen" />
  <img alt="Modes" src="https://img.shields.io/badge/modes-off%20%7C%20heat%20%7C%20cool-informational" />
  <img alt="Units" src="https://img.shields.io/badge/units-%C2%B0C%20%2F%20%C2%B0F-informational" />
  <img alt="HACS" src="https://img.shields.io/badge/HACS-ready-orange" />
</p>

> **Important:** Adaptive Clima enforces a **single global mode** at a time (OFF / HEAT / COOL).  
> It does **not** support one area heating while another is cooling.

---

## What it does ✨

Adaptive Clima creates one master thermostat (`climate.adaptive_clima`) to coordinate multiple area devices,
using your chosen area sensors as the “truth”, while still letting you boost selected areas or combination of areas using **Zones**.

---

## Features ✅

### Master thermostat
- `climate.adaptive_clima` controls all configured Areas
- Modes: **Off / Heat / Cool**
- Presets: `none` + Boost Zones

### Boost Zones
- Each Area automatically becomes a Boost Zone preset
- Create custom zones combining **2 or more** Areas
- Zones are unique (no duplicate combinations)

### Zone Offset
- `number.zone_offset` controls how much warmer or cooler the active zone runs
- Applies immediately when changed

### Actuators
Adaptive Clima supports one actuator per Area:
- **Climate (HVAC)** entities (`climate.*`)
- **Number (setpoint)** entities (`number.*`)
- **Switch (on/off)** entities (`switch.*`)

### Units
Adaptive Clima follows Home Assistant’s global unit system: **°C / °F**.

---

## Installation (HACS) 📦

1. Add this repository as a custom repository in HACS (Integration).
2. Install **Adaptive Clima**.
3. Restart Home Assistant.
4. Settings → Devices & Services → Add integration → **Adaptive Clima**
5. Open **Options** through the cog wheel to configure the Global Settings, Areas and Zones.

---

## Entities created 🧩

| Entity | Type | Description |
|---|---|---|
| `climate.adaptive_clima` | Climate | Master thermostat (icon: `mdi:home-thermometer`). |
| `number.zone_offset` | Number | Boost zone offset (how much warmer or cooler the active zone runs). |
| `switch.include_<area>` | Switch | Include/exclude each Area from control and aggregation. |

---

## Options menu 🛠️

Settings → Devices & services → Adaptive Clima → **Options**

### Global Settings
1) **Setpoint limit** — maximum deviation from center device setpoint
   - The integration will NEVER push a device setpoint more than ±limit around the center.
   - Center = (House target) + (Area bias).
   - Example: Target=25 and Bias=-4 => Center=21. With limit=3, device setpoint stays within [18..24].
     
2) **Unwind threshold** — when to unwind back toward the center
   - When the room is within this distance from the target temperature, the integration starts returning the device setpoint back from the limit toward the center (Target + Bias), to reduce oscillations.
     
3) **Deadband** — tolerance around the target
  - Small zone around target where we try not to toggle switches or over-adjust.
    
4) **Loop interval** — how often the control loop evaluates sensors and decides.
  - It has to be at least half of the change interval for better sampling
    
5) **Minimum change interval** — rate limiting per Area
  - Minimum time between actuator commands (setpoint/toggle).

### Zones
- **Show Zones** — custom zones only (built-in per-area zones hidden)
- **Add Custom Zone** — select 2 or more Areas (cannot include all Areas)
- **Edit/Remove Custom Zone** — manage custom zones
- **Zone Offset** — runtime value is `number.zone_offset`

### About the Bias 🧠

Bias is the difference between the **device setpoint** and your **area sensor**.
It is calculated **Per Area** and it defines what the Area's actuator temperature has to be set to in order to achieve the real desired temperature measured by your Area temperature sensor.

***Device setpoint center = Target + Bias***

Example: If Target is 21°C but the AC must be set to 17°C to keep the room at 21°C => Bias = -4.

- Desired room temperature: **21°**
- Device must be set to: **17°**
- Bias = **-4**

Bias applies to both heating and cooling.

---

## Supported actuator types 🔌

| Actuator | Select in Options | Use cases | Notes |
|---|---|---|---|
| **Climate (HVAC)** | `climate.*` | Mini-split A/C, central HVAC | Adaptive Clima sets mode + target temperature. |
| **Number (setpoint)** | `number.*` | Devices exposing setpoint as a number | Ensure it represents the real device setpoint. |
| **Switch (on/off)** | `switch.*` | Dumb heater/cooler on a smart plug | Uses deadband-based on/off logic. |

---

## Services 🧾

Adaptive Clima uses standard Home Assistant services:

- Master thermostat (`climate.adaptive_clima`)
  - `climate.set_temperature`
  - `climate.set_hvac_mode`
  - `climate.set_preset_mode`
- Zone offset (`number.zone_offset`)
  - `number.set_value`
- Include switches
  - `switch.turn_on`
  - `switch.turn_off`

---

## Troubleshooting 🧯

### After an update the UI text doesn’t appear
Your browser may caches translation resources for your language (xx.json file). Clear website data for your Home Assistant URL and reload.

### Some devices don’t turn on after switching modes

- Confirm the Area include switch is ON
- Confirm `supports_heat` / `supports_cool` are correct
- Confirm the underlying device supports the selected HVAC mode

<br>

----------------------------------------

## License

This project is **source-available** under the **Adaptive Clima License (No Redistribution / No Public Forks)**.
You may use and modify it for personal or internal business purposes, but you may not redistribute it or publish forks.
See the `LICENSE` file.

## Ownership and official distribution

Adaptive Clima is a product of **Primeraid Europe (Private Capital Company – IKE)** (“Primeraid Europe”).

This repository may be hosted under a personal GitHub account for convenience; however, the **licensor and copyright holder**
is Primeraid Europe.

Official distribution is only via:
- this official repository, and
- the official HACS listing that points to it.

## Trademarks

“Adaptive Clima” and “Primeraid Europe” names/logos/branding are not licensed. See `TRADEMARKS.md`.

----------------------------------------
