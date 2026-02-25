# ❄️ Adaptive Clima: Whole-House Adaptive Thermostat with Zones ♨️

<br>

**GLOBAL SETTINGS:**
1) Setpoint limit (°C)
   - The integration will NEVER push a device setpoint more than ±limit around the center.
   - Center = (House target) + (Area bias).
   - Example: Target=25 and Bias=-4 => Center=21. With limit=3, device setpoint stays within [18..24].
     
3) Unwind threshold (°C)
   - When the room is within this distance from the target temperature, the integration starts returning the device setpoint back from the limit toward the center (Target + Bias), to reduce oscillations.
     
5) Deadband (°C)
   - Small zone around target where we try not to toggle switches or over-adjust.
     
7) Loop interval (seconds)
   - How often the control loop evaluates sensors and decides.
     
9) Minimum change interval (seconds)
    - Rate limit per area: minimum time between actuator commands (setpoint/toggle).

IMPORTANT ABOUT BIAS (per area):
Bias is the difference (in °C) that the DEVICE has to be set to in order to achieve the real desired temperature measured by your ROOM SENSOR. Device setpoint center = Target + Bias.
Example: If Target is 21°C but the AC must be set to 17°C to keep the room at 21°C => Bias = -4.

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
