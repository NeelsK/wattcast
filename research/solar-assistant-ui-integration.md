# Solar Assistant — UI & Integration Research

**Investigated:** 2026-05-27  
**Device:** Raspberry Pi 5 Model B Rev 1.1 (rpi64)  
**Local URL:** http://10.69.69.31/  
**Cloud URL:** nkr.za.solar-assistant.io  
**Software version:** 2026-03-24  
**Inverter:** Deye/SunSynk/Sol-Ark (configured, not yet connected — awaiting cable splitter)  
**Location:** Reitz, ZA  
**Storage:** 23% of 16GB used  

---

## Site Structure

| Path | Description |
|---|---|
| `/` | Overview dashboard — live power flow diagram, charts |
| `/#charts` | Jump to charts section on overview |
| `/totals` | 30-day and 12-month energy totals |
| `/power` | Work mode timer + Automations |
| `/inverter/status` | Live inverter status (empty without connection) |
| `/inverter/settings` | Full inverter settings (read/write) |
| `/battery/status` | Live battery status |
| `/grid/status` | Live grid status |
| `/configuration` | Main configuration page |
| `/configuration/mqtt` | MQTT broker configuration |
| `/configuration/advanced` | Advanced: solar PV forecasting, grid, passive reading |

---

## Overview Dashboard (`/`)

Live widgets (all empty without inverter connection):
- Solar PV, Grid, Battery, Load power (W)
- Battery state of charge (%)
- Power flow diagram
- Time-series charts for: Load vs Grid vs Solar PV, Battery power, Battery SoC

---

## Power Management (`/power`)

### Work Mode Timer
- 6 time slots (each with start time, end time, power source: Grid or Gen)
- 13 enable/disable checkboxes
- Links to `/inverter/settings#work-mode` for base work mode settings

### Automations
- Described as: "set inverter settings based on a schedule, battery state of charge, grid outage or other condition"
- No UI controls visible without inverter connection — needs live data to configure triggers

---

## Inverter Settings (`/inverter/settings`)

Full read/write settings page. Sections:

### Specification
- Driver, Serial number, Protocol version, Max AC output power, MPPT connections

### Grid
- Grid voltage high/low
- Grid frequency, high/low limits
- Grid peak shaving + power

### Auxiliary
- Auxiliary port config

### Battery Type
- Battery type, operation mode, capacity

### Battery Charging
- Max discharge current
- Max charge current
- Max grid charge current
- Float charge voltage
- Absorption charge voltage
- Equalization charge voltage

### Work Mode
- Remote switch
- Grid charge
- *(Note: work mode timer on `/power` takes precedence)*

### Work Mode Detail
- Energy pattern
- Max sell power
- Max solar power
- Grid trickle feed

---

## MQTT Configuration (`/configuration/mqtt`)

**This is the primary WattCast integration point.**

| Setting | Current Value |
|---|---|
| Topic prefix | (configurable) |
| Allow setting changes | **Disabled** ← must enable for WattCast writes |
| Reset energy totals | Weekly |
| HomeAssistant Unique ID | (configurable) |
| HA Auto discovery | Disabled |
| Authentication | Username + Password |

**MQTT Broker:** Port 1883, currently **Disabled** (needs to be started)

### MQTT Topic Pattern (Solar Assistant standard)
- **Read data:** `solar_assistant/<device>/<measurement>`
- **Write settings:** `solar_assistant/<device>/set/<setting>`

To enable WattCast integration:
1. Start the MQTT broker (port 1883)
2. Enable "Allow setting changes"
3. WattCast subscribes to data topics for live telemetry
4. WattCast publishes to `set/` topics to change inverter settings

---

## Advanced Configuration (`/configuration/advanced`)

### Solar PV Forecasting Inputs
- Latitude, Longitude
- Tilt, Azimuth
- Temperature Coefficient (Pmax)
- Nominal Module Operating Temperature (NMOT)

### Grid Provider
Options: Default, **South Africa - Eskom**, Europe - EPEX, Europe - NordPool, Octopus Energy, Poland - PSE  
→ Currently set to South Africa - Eskom (relevant for TOU tariff logic in WattCast)

### Inverter (Deye/SunSynk/Sol-Ark)
- Allow passive reading: Yes/No  
  *(Passive = listen to existing WiFi dongle comms rather than poll directly — data may lag a few minutes)*

### Grid Connection
- Auto detect / CT clamp installed / CT clamp not used
- Grid multiplier: Auto detect / 1W (most common) / 10W (newer firmware)

### MPPT
- Auto detect or specify 1–8 connections

### Battery
- Capacity kWh override (used when not readable from battery/inverter)

---

## REST API

No REST API found at standard paths (`/api`, `/api/v1/state`, `/solar_assistant/state`, `/dashboard.json`). **MQTT is the only programmatic integration method.**

---

## Key Takeaways for WattCast

1. **MQTT is the integration layer** — no REST API exists
2. **Setting changes require MQTT** — must enable in `/configuration/mqtt`
3. **Automations** in Solar Assistant itself are basic (schedule/SoC/outage triggers) — WattCast can provide much richer intelligence on top
4. **Eskom TOU** is a recognised grid provider — Solar Assistant may already have tariff data WattCast can leverage
5. **Solar PV forecasting inputs** are configurable — WattCast weather integration can feed into this
6. **Passive reading** may be relevant once the cable splitter arrives — if sharing the line with the WiFi dongle, data could be delayed
7. **MPPT connections** configurable up to 8 — relevant for multi-string setups
