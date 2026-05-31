# Solar Assistant — MQTT Topics Reference

**Broker:** `10.69.69.31:1883`  
**Status:** Disabled by default — enable at `/configuration` → MQTT  
**Auth:** Optional username/password (same local password as web UI)

> **Note:** MQTT must be enabled in Solar Assistant before subscribing. All topics use the prefix `solar_assistant/`.

---

## Quick Connect

```bash
# Subscribe to everything
mosquitto_sub -h 10.69.69.31 -p 1883 -v -t '#'

# Subscribe to solar_assistant topics only
mosquitto_sub -h 10.69.69.31 -p 1883 -v -t 'solar_assistant/#'

# Discover all writable settings (requires HA discovery enabled)
mosquitto_sub -h 10.69.69.31 -v -t '#' | grep command_topic
```

---

## Read Topics (Subscribe)

All topics follow the pattern: `solar_assistant/<device>/<metric>/state`

### Totals (Aggregated)

| Topic | Description | Unit | Notes |
|---|---|---|---|
| `solar_assistant/total/pv_power/state` | Combined PV power | W | All inverters |
| `solar_assistant/total/load_power/state` | Total load power | W | |
| `solar_assistant/total/grid_power/state` | Grid power | W | Negative = export |
| `solar_assistant/total/battery_power/state` | Battery power | W | Negative = charging |
| `solar_assistant/total/battery_state_of_charge/state` | Battery SoC | % | |
| `solar_assistant/total/battery_voltage/state` | Battery voltage | V | |
| `solar_assistant/total/battery_current/state` | Battery current | A | |
| `solar_assistant/total/battery_temperature/state` | Battery temperature | °C | ⚠️ May read 0.0 — CAN BMS quirk |
| `solar_assistant/total/battery_capacity/state` | Battery capacity | Ah | |
| `solar_assistant/total/grid_voltage/state` | Grid voltage | V | |
| `solar_assistant/total/grid_frequency/state` | Grid frequency | Hz | |
| `solar_assistant/total/ac_output_voltage/state` | AC output voltage | V | |
| `solar_assistant/total/ac_output_frequency/state` | AC output frequency | Hz | |
| `solar_assistant/total/inverter_mode/state` | Inverter mode string | — | e.g. "Charge below 60%" |
| `solar_assistant/total/load_percentage/state` | Load as % of capacity | % | |

### Per-Inverter (inverter_1)

| Topic | Description | Unit |
|---|---|---|
| `solar_assistant/inverter_1/pv_power/state` | PV power | W |
| `solar_assistant/inverter_1/grid_power/state` | Grid power | W |
| `solar_assistant/inverter_1/load_power/state` | Load power | W |
| `solar_assistant/inverter_1/load_power_essential/state` | Essential load power | W |
| `solar_assistant/inverter_1/ac_output_power/state` | AC output power | W |
| `solar_assistant/inverter_1/device_mode/state` | Device mode | — |

### Battery (battery_1)

| Topic | Description | Unit |
|---|---|---|
| `solar_assistant/battery_1/voltage/state` | Battery voltage | V |
| `solar_assistant/battery_1/current/state` | Battery current | A |
| `solar_assistant/battery_1/temperature/state` | Battery temperature | °C |

---

## Write Topics (Publish)

**Topic format:** `solar_assistant/inverter_1/<setting>/set`  
**Response topic:** `solar_assistant/set/response_message/state`

```bash
# Generic write
mosquitto_pub -h 10.69.69.31 \
  -t 'solar_assistant/inverter_1/<setting>/set' \
  -m '<value>'
```

### Core Operational Settings

| Topic | Values | Notes |
|---|---|---|
| `inverter_1/work_mode/set` | `"Battery first"` / `"Grid first"` / `"Solar first"` / `"Zero export to load"` | Main operating mode (SunSynk naming — not `output_source_priority`) |
| `inverter_1/energy_pattern/set` | `"Battery first"` / `"Load first"` | Energy priority |
| `inverter_1/use_timer/set` | `"True"` / `"False"` | Enable/disable TOU timer. **Must be True for slot settings to take effect** |
| `inverter_1/grid_charge/set` | `"Enabled"` / `"Disabled"` | Global grid charge switch |
| `inverter_1/solar_export_when_battery_full/set` | `"Enabled"` / `"Disabled"` | Export excess solar |

### TOU Time Slots (6 slots)

| Topic | Format | Example |
|---|---|---|
| `inverter_1/time_point_1/set` | `HH:MM:SS` | `"07:00:00"` |
| `inverter_1/time_point_2/set` | `HH:MM:SS` | `"11:00:00"` |
| `inverter_1/time_point_3/set` | `HH:MM:SS` | `"14:30:00"` |
| `inverter_1/time_point_4/set` | `HH:MM:SS` | `"16:30:00"` |
| `inverter_1/time_point_5/set` | `HH:MM:SS` | `"18:00:00"` |
| `inverter_1/time_point_6/set` | `HH:MM:SS` | `"22:00:00"` |

### Capacity Points — Battery Discharge Floor per Slot (%)

| Topic | Current Value | Notes |
|---|---|---|
| `inverter_1/capacity_point_1/set` | 60 | Minimum SoC before battery stops discharging in slot 1 |
| `inverter_1/capacity_point_2/set` | 60 | |
| `inverter_1/capacity_point_3/set` | 60 | |
| `inverter_1/capacity_point_4/set` | 60 | |
| `inverter_1/capacity_point_5/set` | 60 | |
| `inverter_1/capacity_point_6/set` | 60 | |

### Charge Points — Battery Charge Target per Slot

| Topic | Current Value |
|---|---|
| `inverter_1/charge_point_1/set` | 1 |
| `inverter_1/charge_point_2/set` | 1 |
| `inverter_1/charge_point_3/set` | 1 |
| `inverter_1/charge_point_4/set` | 1 |
| `inverter_1/charge_point_5/set` | 1 |
| `inverter_1/charge_point_6/set` | 1 |

### Grid Charge Points — Enable Grid Charging per Slot

| Topic | Values | Notes |
|---|---|---|
| `inverter_1/grid_charge_point_1/set` | `"True"` / `"False"` | **Requires global `grid_charge: Enabled` too** |
| `inverter_1/grid_charge_point_2/set` | `"True"` / `"False"` | |
| `inverter_1/grid_charge_point_3/set` | `"True"` / `"False"` | |
| `inverter_1/grid_charge_point_4/set` | `"True"` / `"False"` | |
| `inverter_1/grid_charge_point_5/set` | `"True"` / `"False"` | |
| `inverter_1/grid_charge_point_6/set` | `"True"` / `"False"` | |

### Sell Points — Enable Grid Export per Slot

| Topic | Values |
|---|---|
| `inverter_1/sell_point_1/set` | `"True"` / `"False"` |
| `inverter_1/sell_point_2/set` | `"True"` / `"False"` |
| `inverter_1/sell_point_3/set` | `"True"` / `"False"` |
| `inverter_1/sell_point_4/set` | `"True"` / `"False"` |
| `inverter_1/sell_point_5/set` | `"True"` / `"False"` |
| `inverter_1/sell_point_6/set` | `"True"` / `"False"` |

### Power Points — Max Power per Slot (W)

| Topic | Current Value |
|---|---|
| `inverter_1/power_point_1/set` | 5000 |
| `inverter_1/power_point_2/set` | 5000 |
| `inverter_1/power_point_3/set` | 5000 |
| `inverter_1/power_point_4/set` | 5000 |
| `inverter_1/power_point_5/set` | 5000 |
| `inverter_1/power_point_6/set` | 5000 |

### Battery Discharge Control (Safe to Automate)

| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/start_battery_discharge_capacity/set` | 50 | % |
| `inverter_1/start_battery_discharge_voltage/set` | 52.0 | V |
| `inverter_1/stop_battery_discharge_capacity/set` | 35 | % |
| `inverter_1/stop_battery_discharge_voltage/set` | 47.5 | V |

### Grid Charge Control (Safe to Automate)

| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/start_grid_charge_capacity/set` | 30 | % |
| `inverter_1/start_grid_charge_voltage/set` | 49.0 | V |
| `inverter_1/max_grid_charge_current/set` | 40 | A |

### Current / Power Limits (⚠️ Caution)

| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/max_charge_current/set` | 60 | A |
| `inverter_1/max_discharge_current/set` | 115 | A |
| `inverter_1/max_sell_power/set` | 5000 | W |
| `inverter_1/max_solar_power/set` | 6500 | W |

### Shutdown Protection (⚠️ Caution)

| Topic | Current Value | Unit |
|---|---|---|
| `inverter_1/output_shutdown_capacity/set` | 20 | % |
| `inverter_1/output_shutdown_voltage/set` | 46.0 | V |

---

## Code Examples

### Python (paho-mqtt)

```python
import paho.mqtt.client as mqtt

BROKER = "10.69.69.31"
PORT = 1883

def on_connect(client, userdata, flags, rc):
    client.subscribe("solar_assistant/#")

def on_message(client, userdata, msg):
    print(f"{msg.topic}: {msg.payload.decode()}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT)
client.loop_forever()
```

### Python — Write a Setting

```python
import paho.mqtt.publish as publish

publish.single(
    topic="solar_assistant/inverter_1/capacity_point_1/set",
    payload="20",
    hostname="10.69.69.31",
    port=1883
)
```

### Bash — Read one value

```bash
LOAD=$(mosquitto_sub -h 10.69.69.31 -t 'solar_assistant/total/load_power/state' -C 1)
echo "Load: $LOAD W"
```

### Bash — Write a setting

```bash
mosquitto_pub -h 10.69.69.31 \
  -t 'solar_assistant/inverter_1/capacity_point_1/set' \
  -m '20'
```

---

## MQTT Bridge (External Broker)

Add to your broker's `mosquitto.conf` to bridge in all SA topics:

```
connection SolarAssistant
address 10.69.69.31
topic # in
topic solar_assistant/# out
```

---

## Important Notes

1. **`use_timer` must be `True`** for TOU slot (capacity/charge/grid_charge points) to take effect.
2. **Grid charging needs two settings**: global `grid_charge: Enabled` AND the relevant `grid_charge_point_N: True`.
3. **`work_mode` not `output_source_priority`** — this SunSynk inverter uses `work_mode` (Voltronic naming differs).
4. **Battery temperature** reads `0.0°C` — CAN BMS reporting quirk. Do not use as a rule condition.
5. All `_raw` topics (e.g. `force_generator_raw`) are internal — do not write to them.
6. MQTT response to writes comes on: `solar_assistant/set/response_message/state`
7. The full topic list for your specific inverter model: `GET http://10.69.69.31/api/v1/metrics` (returns all 216 metrics).
