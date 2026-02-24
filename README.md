# ha_house_clima
Custom integration (Whole-House Adaptive Thermostat with Zones)


GLOBAL SETTINGS:
1) Setpoint limit (°C)   - The integration will NEVER push a device setpoint more than ±limit around the center.   - Center = (House target) + (Area bias).   - Example: Target=25 and Bias=-4 => Center=21. With limit=3, device setpoint stays within [18..24].
2) Unwind threshold (°C)   - When the room is within this distance from the target temperature, the integration starts returning     the device setpoint back from the limit toward the center (Target + Bias), to reduce oscillations.
3) Deadband (°C)   - Small zone around target where we try not to toggle switches or over-adjust.
4) Loop interval (seconds)   - How often the control loop evaluates sensors and decides.
5) Minimum change interval (seconds)   - Rate limit per area: minimum time between actuator commands (setpoint/toggle).

IMPORTANT ABOUT BIAS (per area):
Bias is the difference (in °C) that the DEVICE has to be set to in order to achieve the real desired temperature measured by your ROOM SENSOR. Device setpoint center = Target + Bias.
Example: If Target is 21°C but the AC must be set to 17°C to keep the room at 21°C => Bias = -4.

