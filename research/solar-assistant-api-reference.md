# Solar Assistant — Full API Reference

**Source:** solar-assistant.io/help/integration/  
**Researched:** 2026-05-27  
**Device instance:** http://10.69.69.31/ (nkr.za.solar-assistant.io)

---

## Overview of Integration Options

| Method | Use Case | Direction |
|---|---|---|
| REST API | Poll current metrics, write settings | Read + Write |
| WebSocket API | Stream live metrics in real-time | Read (push) |
| MQTT API | Real-time streaming, setting changes | Read + Write |
| Cloud API | Remote access without local network | Read + Write (via proxy) |

---

## 1. REST API

**Base URL (local):** `http://10.69.69.31/api/v1/metrics`

### Authentication
- Basic auth: `admin:<password>` (password set at `/configuration` → local password)
- Bearer token: `Authorization: Bearer <token>`

### Read — GET /api/v1/metrics

```bash
# All metrics
curl -u admin:<password> http://10.69.69.31/api/v1/metrics

# Filter by topic glob
curl -u admin:<password> "http://10.69.69.31/api/v1/metrics?topic=battery*"
curl -u admin:<password> "http://10.69.69.31/api/v1/metrics?topic=total/*"

# Single value as plain text
curl -u admin:<password> "http://10.69.69.31/api/v1/metrics?topic=total/load_power&value=1"
```

**Example JSON response:**
```json
[
  {"topic": "total/pv_power", "group": "Status", "name": "PV power", "value": 1240, "unit": "W"},
  {"topic": "total/load_power", "group": "Status", "name": "Load power", "value": 940, "unit": "W"},
  {"topic": "total/battery_state_of_charge", "group": "Status", "name": "Battery state of charge", "value": 80, "unit": "%"}
]
```

### Write — POST /api/v1/metrics

```bash
curl -u admin:<password> -X POST http://10.69.69.31/api/v1/metrics \
  -H "Content-Type: application/json" \
  -d '{"topic": "inverter_1/output_source_priority", "value": "Utility first"}'
```

**Success response:**
```json
{"topic": "inverter_1/output_source_priority", "result": "ok"}
```
**Failure:** HTTP 422 with error message.

> The topic format for writes is the same as for reads — use the `topic` field from a GET response to know what to write back.

---

## 2. WebSocket API

**URL:** `ws://10.69.69.31/api/socket/websocket?password=<password>`

Built on **Phoenix Channels**. Client libraries available for Python, JavaScript, C#, Java/Kotlin, Swift.

### Connect & Join

```json
{"topic": "metrics", "event": "join", "payload": {}, "ref": "1"}
```

Or with topic filter:
```json
{
  "topic": "metrics",
  "event": "join",
  "payload": {"topics": [{"topic": "total/*"}, {"topic": "battery_1/*"}]},
  "ref": "1"
}
```

Change topic filter after joining:
```json
{"topic": "metrics", "event": "topics", "payload": {"topics": [{"topic": "*"}]}, "ref": "2"}
```

Throttle update frequency:
```json
{"topics": [{"topic": "total/*", "max_frequency_s": 5}]}
```

### Server Messages

**Definition** (sent once per topic, with metadata):
```json
{
  "event": "definition",
  "payload": {"definitions": [
    {"topic": "total/pv_power", "device": "Totals", "group": "Status", "name": "PV power", "unit": "W"}
  ]}
}
```

**Data** (sent each time values update):
```json
{
  "event": "data",
  "payload": {"metrics": [
    {"topic": "total/pv_power", "value": 1240},
    {"topic": "total/load_power", "value": 940}
  ]}
}
```

> **Best for WattCast real-time dashboard** — no polling needed, push updates on change.

---

## 3. MQTT API

**Broker:** `10.69.69.31:1883` (currently disabled — enable at `/configuration`)

### Subscribe to all topics
```bash
mosquitto_sub -h 10.69.69.31 -p 1883 -v -t '#'
```

### Write a setting
```bash
# Topic format: solar_assistant/<device>/<setting>/set
mosquitto_pub -h 10.69.69.31 -t 'solar_assistant/inverter_1/output_source_priority/set' -m 'Utility first'
```

**Response topic:** `solar_assistant/set/response_message/state`

### Deye/SunSynk specific examples
```bash
# Max AC charge current to 20A
mosquitto_pub -h 10.69.69.31 -t 'solar_assistant/inverter_1/max_grid_charge_current/set' -m '20'

# Work mode capacity point 1 to 15%
mosquitto_pub -h 10.69.69.31 -t 'solar_assistant/inverter_1/capacity_point_1/set' -m '15'
```

### Discover all writable settings
Enable HomeAssistant discovery then:
```bash
mosquitto_sub -h 10.69.69.31 -v -t '#' | grep command_topic
```

### MQTT Bridge (for external broker)
Add to your broker's mosquitto config:
```
connection SolarAssistant
address 10.69.69.31
topic # in
topic solar_assistant/# out
```

### Script usage (from Pi via SSH)
```bash
#!/bin/bash
LOAD_POWER=$(mosquitto_sub -h localhost -t 'solar_assistant/inverter_1/load_power' -C 1)
echo "Load power is: $LOAD_POWER"
```

---

## 4. Cloud API

**Base URL:** `https://solar-assistant.io/api/v1/`  
**Auth:** Bearer token (generate at solar-assistant.io/user/edit#api)

### List sites
```bash
curl -H "Authorization: Bearer <token>" https://solar-assistant.io/api/v1/sites
```

### Generate site access token
```bash
curl -X POST -H "Authorization: Bearer <token>" \
  https://solar-assistant.io/api/v1/sites/<site-id>/authorize
```

**Response:**
```json
{
  "host": "us-htz-1.solar-assistant.io",
  "site_id": 19489,
  "site_key": "9u10PW2b...",
  "token": "eyJhbGci..."
}
```
Token expires after **7 days**.

### Proxy pass-through (access full REST/WebSocket API remotely)
```bash
curl \
  -H "Authorization: Bearer <token>" \
  -H "Site-Id: <site-id>" \
  -H "Site-Key: <site-key>" \
  https://<host>/api/v1/metrics
```

**Neels' site ID:** `nkr.za.solar-assistant.io` (find numeric ID from `/api/v1/sites`)

> **Best for WattCast cloud/remote access** — allows WattCast to control the inverter from anywhere, not just local network.

---

## Common Topics (all APIs share the same topic structure)

| Topic | Description | Unit |
|---|---|---|
| `total/pv_power` | Combined PV power (all inverters) | W |
| `total/load_power` | Total load power | W |
| `total/grid_power` | Grid power (negative = export) | W |
| `total/battery_power` | Battery power (negative = charging) | W |
| `total/battery_state_of_charge` | Battery SoC | % |
| `inverter_1/device_mode` | Current inverter mode | — |
| `inverter_1/output_source_priority` | Output source priority | — |
| `inverter_1/max_grid_charge_current` | Max AC charge current | A |
| `inverter_1/capacity_point_1` | Work mode SoC threshold 1 | % |
| `battery_1/voltage` | Battery voltage | V |

> Call `GET /api/v1/metrics` without filter once inverter is connected to get the full topic list for your specific Deye/SunSynk model.

---

## WattCast Integration Strategy

| Need | Method | Notes |
|---|---|---|
| Real-time monitoring (local) | WebSocket | Push updates, filter to needed topics, throttle with max_frequency_s |
| Snapshot polling (local) | REST GET | Simpler, use for periodic checks |
| Write inverter settings | REST POST or MQTT pub | REST simpler; MQTT if broker already running |
| Remote access / cloud | Cloud API → proxy | Get site token, then use same REST/WS paths |
| Discover all writable settings | MQTT + HA discovery | Run once to enumerate all settable topics |

**Pre-requisite:** Set a local password at `/configuration` → local password before REST/WebSocket APIs will work.

---

## Complete Inverter Settings — Live Snapshot (2026-05-28)

Retrieved via `GET /api/v1/metrics` on Neels' Deye/SunSynk 8kW hybrid.  
Total metrics: 216. Settings group: 113.  
These are the exact MQTT topic names to use for reading and writing.

### TOU Time Slots (6 slots, all configurable)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/time_point_1` | 00:00:00 | hhmm |
| `inverter_1/time_point_2` | 07:00:00 | hhmm |
| `inverter_1/time_point_3` | 11:00:00 | hhmm |
| `inverter_1/time_point_4` | 14:30:00 | hhmm |
| `inverter_1/time_point_5` | 16:30:00 | hhmm |
| `inverter_1/time_point_6` | 18:00:00 | hhmm |

### Capacity Points — Battery Discharge Floor per Slot
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/capacity_point_1` | 60 | % |
| `inverter_1/capacity_point_2` | 60 | % |
| `inverter_1/capacity_point_3` | 60 | % |
| `inverter_1/capacity_point_4` | 60 | % |
| `inverter_1/capacity_point_5` | 60 | % |
| `inverter_1/capacity_point_6` | 60 | % |

### Charge Points — Battery Charge Target per Slot
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/charge_point_1` | 1 | |
| `inverter_1/charge_point_2` | 1 | |
| `inverter_1/charge_point_3` | 1 | |
| `inverter_1/charge_point_4` | 1 | |
| `inverter_1/charge_point_5` | 1 | |
| `inverter_1/charge_point_6` | 1 | |

### Grid Charge Points — Enable Grid Charging per Slot
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/grid_charge_point_1` | True | Enabled |
| `inverter_1/grid_charge_point_2` | True | Enabled |
| `inverter_1/grid_charge_point_3` | True | Enabled |
| `inverter_1/grid_charge_point_4` | True | Enabled |
| `inverter_1/grid_charge_point_5` | True | Enabled |
| `inverter_1/grid_charge_point_6` | True | Enabled |

### Sell Points — Enable Grid Export per Slot
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/sell_point_1` | False | Enabled |
| `inverter_1/sell_point_2` | False | Enabled |
| `inverter_1/sell_point_3` | False | Enabled |
| `inverter_1/sell_point_4` | False | Enabled |
| `inverter_1/sell_point_5` | False | Enabled |
| `inverter_1/sell_point_6` | False | Enabled |

### Power Points — Max Power per Slot
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/power_point_1` | 5000 | W |
| `inverter_1/power_point_2` | 5000 | W |
| `inverter_1/power_point_3` | 5000 | W |
| `inverter_1/power_point_4` | 5000 | W |
| `inverter_1/power_point_5` | 5000 | W |
| `inverter_1/power_point_6` | 5000 | W |

### Voltage Points — per Slot
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/voltage_point_1` | 49.0 | V |
| `inverter_1/voltage_point_2` | 49.0 | V |
| `inverter_1/voltage_point_3` | 49.0 | V |
| `inverter_1/voltage_point_4` | 49.0 | V |
| `inverter_1/voltage_point_5` | 49.0 | V |
| `inverter_1/voltage_point_6` | 49.0 | V |

### Program Points — per Slot
| Topic | Current Value |
|---|---|
| `inverter_1/program_point_1` | None |
| `inverter_1/program_point_2` | None |
| `inverter_1/program_point_3` | None |
| `inverter_1/program_point_4` | None |
| `inverter_1/program_point_5` | None |
| `inverter_1/program_point_6` | None |

### Core Operational Settings (Safe — Green)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/use_timer` | True | Enabled |
| `inverter_1/work_mode` | Zero export to load | |
| `inverter_1/energy_pattern` | Battery first | |
| `inverter_1/grid_charge` | Enabled | |
| `inverter_1/solar_export_when_battery_full` | Disabled | |

### Battery Discharge Control (Safe — Green)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/start_battery_discharge_capacity` | 50 | % |
| `inverter_1/start_battery_discharge_voltage` | 52.0 | V |
| `inverter_1/stop_battery_discharge_capacity` | 35 | % |
| `inverter_1/stop_battery_discharge_voltage` | 47.5 | V |

### Grid Charge Control (Safe — Green)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/start_grid_charge_capacity` | 30 | % |
| `inverter_1/start_grid_charge_voltage` | 49.0 | V |

### Current Limits (Caution — ⚠️)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/max_charge_current` | 60 | A |
| `inverter_1/max_discharge_current` | 115 | A |
| `inverter_1/max_grid_charge_current` | 40 | A |
| `inverter_1/max_generator_charge_current` | 40 | A |
| `inverter_1/max_sell_power` | 5000 | W |
| `inverter_1/max_solar_power` | 6500 | W |

### Shutdown Protection (Caution — ⚠️)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/output_shutdown_capacity` | 20 | % |
| `inverter_1/output_shutdown_voltage` | 46.0 | V |

### Grid Protection (Caution — ⚠️)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/grid_voltage_high` | 265.0 | V |
| `inverter_1/grid_voltage_low` | 185.0 | V |
| `inverter_1/grid_frequency_high` | 51.5 | Hz |
| `inverter_1/grid_frequency_low` | 48.0 | Hz |
| `inverter_1/grid_frequency` | 50 Hz | |
| `inverter_1/grid_type` | 220/230/240V Single phase | |
| `inverter_1/grid_trickle_feed` | 20 | W |
| `inverter_1/grid_peak_shaving` | Disabled | |
| `inverter_1/grid_peak_shaving_power` | 8000 | W |

### Battery Charge Voltages (Caution — ⚠️)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/battery_absorption_charge_voltage` | 57.6 | V |
| `inverter_1/battery_equalization_charge_voltage` | 57.6 | V |
| `inverter_1/battery_float_charge_voltage` | 53.5 | V |

### Restricted Settings (🔒 — Hardware/Protocol Level)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/battery_type` | Lithium | |
| `inverter_1/battery_operation` | State of charge | |
| `inverter_1/battery_capacity` | 300 | Ah |
| `inverter_1/lithium_protocol` | CAN (protocol 0) | |
| `inverter_1/remote_switch` | On | |
| `inverter_1/parallel` | Disabled | |
| `inverter_1/modbus_number` | 1 | |

### Auxiliary Port / Generator (Restricted — 🔒)
| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/auxiliary_port` | Generator input | |
| `inverter_1/auxiliary_port_high_capacity` | 100 | % |
| `inverter_1/auxiliary_port_high_voltage` | 54.0 | V |
| `inverter_1/auxiliary_port_low_capacity` | 95 | % |
| `inverter_1/auxiliary_port_low_voltage` | 51.0 | V |
| `inverter_1/auxiliary_load_output_on_grid_always_on` | Disabled | |
| `inverter_1/force_generator_on` | Disabled | |
| `inverter_1/generator_charge` | Disabled | |
| `inverter_1/generator_connected_to_grid_input` | Disabled | |
| `inverter_1/generator_down_time` | 0.0 | h |
| `inverter_1/generator_max_run_time` | 24.0 | h |
| `inverter_1/generator_peak_shaving` | Disabled | |
| `inverter_1/generator_peak_shaving_power` | 8000 | W |
| `inverter_1/generator_start_capacity` | 10 | % |
| `inverter_1/generator_start_voltage` | 0.0 | V |
| `inverter_1/generator_stop_capacity` | 100 | % |
| `inverter_1/generator_stop_voltage` | 54.0 | V |
| `inverter_1/gen_charge_point_1..6` | False | Enabled |

### Raw/Internal (Do Not Touch)
| Topic | Current Value |
|---|---|
| `inverter_1/force_generator_raw` | 0 |
| `inverter_1/parallel_info_raw` | 1024 |
| `inverter_1/peak_shaving_raw` | 0 |
| `inverter_1/inverter_time` | 21:10:00 |

### Status Metrics (Read-Only — from same API call)
| Topic | Value | Unit |
|---|---|---|
| `total/battery_state_of_charge` | 90.0 | % |
| `total/battery_voltage` | 49.6 | V |
| `total/battery_current` | -8.85 | A |
| `total/battery_power` | -441 | W |
| `total/battery_temperature` | 0.0 | °C |
| `total/battery_capacity` | 300 | Ah |
| `total/pv_power` | 1 | W |
| `total/load_power` | 406 | W |
| `total/load_percentage` | 8 | % |
| `total/grid_power` | 22 | W |
| `total/grid_voltage` | 239.0 | V |
| `total/grid_frequency` | 50.13 | Hz |
| `total/ac_output_voltage` | 238.9 | V |
| `total/ac_output_frequency` | 50.13 | Hz |
| `total/inverter_mode` | Charge below 60% | |
| `inverter_1/ac_output_power` | 384 | W |
| `inverter_1/load_power_essential` | 406 | W |

### Key Observations for WattCast

1. **All 6 TOU slots** have separate capacity, charge, grid_charge, sell, power, voltage, and program points — 7 dimensions per slot × 6 slots = 42 slot-related settings alone.
2. **`use_timer: True`** — timer is active, meaning TOU slot settings are in effect. If use_timer were False, the inverter would ignore capacity/charge points.
3. **`work_mode: Zero export to load`** — matches installer config. Other values: "Battery first", "Grid first", "Solar first".
4. **`energy_pattern: Battery first`** — overall energy priority.
5. **`battery_temperature: 0.0°C`** — this is suspicious, likely a BMS reporting quirk (CAN protocol). Do not use battery temperature as a condition in rules.
6. **Generator settings are present but unused** — `auxiliary_port: Generator input` but all generator settings are at defaults/disabled. Safe to ignore for now.
7. **No `output_source_priority` topic** — this inverter uses `work_mode` instead (SunSynk-specific naming vs older Voltronic/Axpert naming).
8. **`grid_charge: Enabled` AND `grid_charge_point_1..6: True`** — both global and per-slot grid charge are on. Need both to enable grid charging for a slot.
