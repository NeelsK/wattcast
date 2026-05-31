# Solar Assistant — Architecture & Integration Research

**Source:** Analysis of `pi_root_extracted` filesystem + official documentation  
**Version found:** solar_assistant-1.9.15  
**Date researched:** 2026-05-25

---

## What Solar Assistant Is

Solar Assistant is a monitoring and control application that runs on a Raspberry Pi (or compatible Orange Pi). It:
- Connects to solar inverters and battery BMSs over RS-485/Modbus, CAN bus, or USB
- Exposes all data via MQTT (built-in broker on port 1883), REST API, WebSocket API, and a local web UI
- Provides a kiosk-mode full-screen Chromium dashboard pointed at `http://127.0.0.1`
- Optionally tunnels to the cloud via **frpc** (FRP reverse proxy) for remote access

---

## Tech Stack (from binary analysis)

- **Language:** Elixir / Erlang OTP (compiled into a single BEAM release, then wrapped into a Linux binary via Burrito/escript)
- **Web framework:** Phoenix LiveView (`phoenix`, `plug`, `cowboy`)
- **Database:** Mnesia (Erlang's built-in distributed DB) — three tables:
  - `Elixir.Database` — general key/value store
  - `Elixir.Database.Setting` — configuration settings (`name`, `value`)
  - `Elixir.Database.UserToken` — auth tokens (`user`, `context`, `token`, `expire`)
- **MQTT broker:** Built-in (Mosquitto-compatible), port 1883
- **HTTP client:** Hackney
- **JSON:** Jason
- **CSV export:** NimbleCSV
- **Connectivity modules:** Diagnostic.Broker.Modbus, CAN bus driver

---

## Inverter / Battery Support (confirmed in binary)

**Inverters:**
- Sunsynk / Deye (same hardware, very common in SA)
- Growatt SPH series
- GoodWe
- Luxpower / LuxPower (3-phase, hybrid, split-phase variants)
- Voltronic / Axpert / Infini
- Solis
- SRNE
- EG4 / Sol-Ark
- MUST / EASUN / Sumry / Anenji / PowMr / Megarevo
- Sungrow / Huawei (newer additions)

**Batteries / BMS:**
- Pylontech / Revov / Seplos / SolarMD
- Dyness / Mecer
- JK BMS, JBD/Overkill, Daly BMS
- Felicity Solar, SOK, SunSynk battery
- CAN bus generic (many LiFePO4 batteries)
- VE.Direct (Victron)

---

## Connectivity / Physical Interfaces

- RS-485 serial (Modbus RTU) — primary inverter protocol
- CAN bus — battery BMS protocol
- USB serial adapters (`/soc/spi@50`, hidraw, UART)
- WiFi (wlan0) + Ethernet (eth0)
- Bluetooth — for initial setup, with NAT forwarding via `pan0` interface
- Auto-hotspot mode when no WiFi is found

---

## MQTT API (the key integration point for WattCast)

**Broker:** `solar-assistant.local:1883` (or IP:1883)  
**Auth:** optional username/password

### Topic Structure

All topics prefixed with `solar_assistant/`:

| Topic | Description | Unit |
|-------|-------------|------|
| `solar_assistant/total/pv_power/state` | Combined PV power | W |
| `solar_assistant/total/load_power/state` | Total load power | W |
| `solar_assistant/total/grid_power/state` | Grid power (negative = export) | W |
| `solar_assistant/total/battery_power/state` | Battery power (negative = charging) | W |
| `solar_assistant/total/battery_state_of_charge/state` | Battery SoC | % |
| `solar_assistant/inverter_1/pv_power/state` | Per-inverter PV | W |
| `solar_assistant/inverter_1/grid_power/state` | Per-inverter grid | W |
| `solar_assistant/inverter_1/load_power/state` | Per-inverter load | W |
| `solar_assistant/inverter_1/device_mode/state` | Inverter mode (e.g. "Battery first") | - |
| `solar_assistant/inverter_1/output_source_priority/state` | Current priority setting | - |
| `solar_assistant/battery_1/voltage/state` | Battery voltage | V |
| `solar_assistant/battery_1/current/state` | Battery current | A |
| `solar_assistant/battery_1/temperature/state` | Battery temperature | °C |

### Writable Settings (via MQTT)

Publish to `solar_assistant/inverter_1/<setting>/set`:

| Setting key | Example value | Description |
|------------|---------------|-------------|
| `output_source_priority` | `"Utility first"` / `"Battery first"` / `"Solar first"` | Load priority |
| `charger_source_priority` | `"Solar and utility simultaneously"` | Charge source |
| `max_grid_charge_current` | `"20"` | Max AC charge current (A) |
| `shutdown_battery_voltage` | `"47.0"` | Low battery cutoff (V) |
| `capacity_point_1` | `"15"` | SoC threshold 1 (%) — Deye/Sunsynk |

Response comes back on: `solar_assistant/set/response_message/state`

To discover all writable settings for your inverter:
```bash
mosquitto_sub -h solar-assistant.local -v -t '#' | grep command_topic
```

---

## REST API

**Base URL:** `http://<device-ip>/api/v1/`  
**Auth:** Basic auth (`admin:<password>`) or Bearer token

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/metrics` | All current metrics as JSON |
| `GET /api/v1/metrics?topic=battery*` | Filtered by glob |
| `GET /api/v1/metrics?topic=total/load_power&value=1` | Single value as plain text |

**Example JSON response:**
```json
[
  {"topic": "total/pv_power", "group": "Status", "name": "PV power", "value": 1240, "unit": "W"},
  {"topic": "total/load_power", "group": "Status", "name": "Load power", "value": 940, "unit": "W"},
  {"topic": "total/battery_state_of_charge", "group": "Status", "name": "Battery state of charge", "value": 80, "unit": "%"}
]
```

---

## Cloud / Remote Access

- **frpc** (FRP reverse proxy) tunnels the device to solar-assistant.io cloud
- Service alias: `unattend-upgrade.service` (disguised in systemd)
- **Cloud API** available at `https://solar-assistant.io` for remote polling
- sacli (command-line tool) supports cloud authorization

---

## What WattCast Can Use From Solar Assistant

### Read (monitoring):
- Real-time PV generation, load, grid import/export, battery SoC and power
- Device mode / operating status
- Historical data via REST API metrics

### Write (control):
- Switch inverter mode (Solar first / Battery first / Utility first)
- Adjust battery charge/discharge SoC thresholds
- Set max grid charge current
- Time-based charge/discharge settings (Deye/Sunsynk capacity points)

### Integration approach options:
1. **MQTT subscriber** — subscribe to `solar_assistant/#` for real-time data stream
2. **REST API polling** — `GET /api/v1/metrics` for current state snapshot
3. **MQTT publish** — send settings changes directly to `solar_assistant/inverter_1/<setting>/set`

---

## Networking Architecture on the Pi

```
Internet ←→ frpc (reverse proxy) ←→ solar-assistant.io cloud
               ↑
Local network ←→ influx-bridge (Elixir app, port 80/1883)
               ↑
RS-485/CAN bus ←→ Inverter / BMS
```

- The kiosk (Chromium in fullscreen) opens `http://127.0.0.1` 
- The main app listens on port 80 (HTTP) and 1883 (MQTT)
- Bluetooth provides a `pan0` NAT interface for mobile setup

