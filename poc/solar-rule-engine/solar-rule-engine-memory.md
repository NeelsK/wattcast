# Solar Rule Engine — Project Memory

## What This Is
A Python-based rule engine running on a Raspberry Pi that integrates solar system readings, weather station data, weather forecasts, and inverter settings to make automated decisions — including controlling Wi-Fi switches like a Shelly geyser switch.

---

## Infrastructure

| Component | Detail |
|---|---|
| Inverter | Sunsynk (Deye-compatible) |
| Solar management | Solar Assistant on its own Raspberry Pi |
| Inverter comms | Solar Assistant via RS485/local, exposes MQTT |
| Rule engine host | Separate Raspberry Pi |
| MQTT broker | Mosquitto (on rule engine Pi), bridged to Solar Assistant |
| Weather station | Ecowitt (local HTTP or cloud REST API) |
| Forecast | Open-Meteo (free, no API key, `global_tilted_irradiance` endpoint) |
| Smart switches | Shelly (Gen1 and Gen2 supported); generic HTTP (Tasmota etc.) |
| Home automation | Home Assistant running on Unraid (separate, not part of this engine) |

---

## Architecture

```
Solar Assistant Pi  ──MQTT bridge──►  Mosquitto (rule engine Pi)
                                              │
                                    ┌─────────▼──────────────┐
                                    │   Python Rule Engine    │
                                    │                         │
                                    │  sources/ecowitt.py     │◄── Ecowitt local HTTP
                                    │  sources/openmeteo.py   │◄── Open-Meteo forecast
                                    │  mqtt_client.py         │◄── Solar Assistant MQTT
                                    │  engine.py (pure)       │
                                    │  hysteresis.py          │
                                    │  switches/shelly.py     │──► Shelly local HTTP
                                    └─────────────────────────┘
```

---

## Project File Structure

```
solar-rule-engine/
├── config.yaml                  # Per-installation config (location, panels, thresholds)
├── main.py                      # Entry point; wires all components together
├── engine.py                    # Pure evaluate() decision function — no I/O
├── models.py                    # Dataclasses: SystemState, WeatherNow, ForecastSummary, Command, SwitchAction
├── hysteresis.py                # Prevents inverter setting flip-flopping (min interval per topic)
├── mqtt_client.py               # MQTT subscribe (Solar Assistant) + publish (inverter commands)
├── requirements.txt             # paho-mqtt, requests, PyYAML
├── solar-rule-engine.service    # systemd service file for Pi
├── sources/
│   ├── ecowitt.py               # Local HTTP or cloud REST; both return normalised WeatherNow
│   └── openmeteo.py             # GTI forecast fetch + kWh yield estimation
├── switches/
│   ├── shelly.py                # ShellySwitch: Gen1 (GET /relay/0) and Gen2 (RPC)
│   ├── manager.py               # SwitchManager: name-keyed registry; GenericHTTPSwitch fallback
│   └── state_tracker.py         # SwitchRunState: min on/off time, daily budget, day rollover
└── tests/
    └── test_engine.py           # 20 unit tests on engine.py — no hardware required
```

---

## Key Design Principles

- **`engine.evaluate()` is pure** — takes dataclasses, returns `(list[Command], list[SwitchAction])`, no I/O. Fully unit-testable with synthetic data.
- **Hysteresis on inverter commands** — minimum 15 min between same-topic changes (configurable).
- **Switch guards in `state_tracker`** — minimum on-time, minimum off-time, daily budget. Engine decides; tracker gates.
- **Ecowitt dual-source** — `config.ecowitt.source: local | cloud`. Both paths return the same `WeatherNow`. Same codebase for all installations.
- **Open-Meteo GTI** — pass `tilt` and `azimuth` to API; it does the panel geometry. Values are backwards-averaged over the preceding hour.
- **`dry_run: true` in config** — logs all commands without sending. Always start here.

---

## Solar Assistant MQTT

- **Broker**: runs on the Solar Assistant Pi itself
- **Bridge**: Mosquitto on rule engine Pi bridges to Solar Assistant broker
- **Subscribe topics**: `solar_assistant/inverter_1/#` and `solar_assistant/total/#`
- **Key read topics**:
  - `solar_assistant/total/battery_state_of_charge/state`
  - `solar_assistant/inverter_1/pv_power/state`
  - `solar_assistant/inverter_1/load_power/state`
  - `solar_assistant/inverter_1/grid_power/state`
  - `solar_assistant/inverter_1/battery_power/state`
- **Write topics** (Deye/Sunsynk compatible):
  - `solar_assistant/inverter_1/max_grid_charge_current/set`
  - `solar_assistant/inverter_1/capacity_point_1/set`
- **Payload**: plain string value (e.g. `"20"` for 20A)

---

## Ecowitt Integration

### Local (default)
```
GET http://<station_ip>/get_livedata_info
```
Sensor IDs: `0x02` = temp, `0x07` = humidity, `0x0B` = wind speed, `0x17` = irradiance, `0x0D` = rain rate.

### Cloud
```
GET https://api.ecowitt.net/api/v3/device/real_time
Params: application_key, api_key, mac, call_back=all
```

Config key: `ecowitt.source: local | cloud`

---

## Open-Meteo Forecast

```
GET https://api.open-meteo.com/v1/forecast
Params: latitude, longitude, timezone, tilt, azimuth, forecast_days=2
Hourly variables: global_tilted_irradiance, precipitation, precipitation_probability, cloud_cover, temperature_2m
```

Yield estimation formula:
```
yield_kwh = (gti_wm2 / 1000) × panel_kwp × 0.80
```
Efficiency 0.80 = inverter (0.96) × cables (0.98) × temperature (0.95) × soiling (0.97).

---

## Shelly Switch API

| | Gen1 | Gen2 |
|---|---|---|
| Turn on | `GET /relay/0?turn=on` | `GET /rpc/Switch.Set?id=0&on=true` |
| Turn off | `GET /relay/0?turn=off` | `GET /rpc/Switch.Set?id=0&on=false` |
| Status | `GET /relay/0` → `ison` field | `GET /rpc/Switch.GetStatus?id=0` → `output` field |
| Auth | HTTP Basic (user:pass in URL) | HTTP Digest |
| Auto-detect | `GET /shelly` → `gen` field | same |

---

## Engine Decision Logic

### Inverter commands produced each cycle
1. `capacity_point_1` — SOC floor based on tomorrow's forecast yield
2. `max_grid_charge_current` — grid charge rate based on forecast + current SOC + weather

### SOC floor rules
| Condition | Floor |
|---|---|
| Tomorrow yield < `forecast_poor_kwh` (8 kWh default) | `soc_target_rainy_day` (90%) |
| Tomorrow yield > `forecast_good_kwh` (15 kWh) AND rain < 60% | `soc_target_good_day` (50%) |
| Otherwise | `soc_target_default` (70%) |
| Hard minimum | `battery.min_soc` (never goes below) |

### Grid charge rules (evaluated in priority order)
1. Raining + SOC < default target → full grid charge (emergency)
2. Poor forecast + SOC < rainy target → scaled charge (deficit-proportional)
3. Good forecast + SOC > floor → charge off
4. Afternoon (≥13:00) + remaining solar > 3 kWh → charge off
5. Night + moderate forecast → 50% of max charge
6. Default → charge off

### Switch roles
| Role | Logic |
|---|---|
| `geyser` | ON when surplus > element_watts + margin AND SOC > min AND in window AND not raining |
| `pool_pump` | ON during peak solar hours with surplus > pump_watts |
| `generic_load` | ON when surplus > load_watts AND in time window |

### Switch guards (in `state_tracker.py`, not engine)
- `min_on_minutes` — minimum run before allowing turn-off (prevents short-cycling)
- `max_daily_minutes` — daily run budget
- `min_off_minutes` — minimum rest before allowing turn-on

---

## Config Keys Reference

```yaml
location:
  latitude, longitude, timezone

panels:
  total_kwp, tilt, azimuth

battery:
  capacity_kwh, min_soc, max_soc

mqtt:
  host, port, username, password, inverter_id

ecowitt:
  source (local|cloud), station_ip, app_key, api_key, mac_address

engine:
  eval_interval_seconds   # default 300
  forecast_refresh_minutes  # default 60
  hysteresis_minutes      # default 15
  dry_run                 # default true — ALWAYS start true
  log_level

thresholds:
  soc_target_rainy_day, soc_target_good_day, soc_target_default
  grid_charge_max_a, grid_charge_min_a, grid_charge_off
  forecast_good_kwh, forecast_poor_kwh
  rain_probability_high

switches:
  - name, type (shelly|http), ip, gen (1|2), channel, role
    role=geyser: element_watts, min_soc_to_run, surplus_margin_watts, window_start_hour, window_end_hour
    role=pool_pump: pump_watts, min_soc_to_run, window_start_hour, window_end_hour
    role=generic_load: load_watts, min_soc_to_run, window_start_hour, window_end_hour
    all switches: min_on_minutes, max_daily_minutes, min_off_minutes
```

---

## Deployment on Raspberry Pi

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Verify engine logic (no hardware needed)
pytest tests/test_engine.py -v

# Run with dry_run: true first
python main.py --config config.yaml

# Install as service
sudo cp solar-rule-engine.service /etc/systemd/system/
sudo systemctl enable --now solar-rule-engine
journalctl -u solar-rule-engine -f
```

---

## Extending the Engine

### Add a new inverter command
Add a `Command` to the `commands` list in `engine.py`. Add a test in `test_engine.py`.

### Add a new switch role
Add `_evaluate_<role>()` in `engine.py`, register it in `_evaluate_switch()`. Add switch config keys to `config.yaml`.

### Add a new switch type (non-Shelly)
Implement a class with `turn_on(reason)`, `turn_off(reason)`, `get_state()` in `switches/`. Register in `SWITCH_DRIVERS` in `switches/manager.py`.

### Add a new data source
Create `sources/<name>.py` with a `fetch(config) -> <dataclass>` function. Add the dataclass to `models.py`. Call from `main.py`.
