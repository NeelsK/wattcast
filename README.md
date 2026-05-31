# WattCast

Solar intelligence for South African homes. WattCast runs on a Raspberry Pi and uses live inverter data, weather station readings, and solar forecasts to automatically optimise your solar system — adjusting inverter settings and controlling smart switches to maximise self-consumption and protect your battery.

## What's in this repo

### `mqtt_bridge/`
Subscribes to the Solar Assistant MQTT broker, buffers live inverter readings, and flushes one row per minute to a MariaDB database. Also handles Telegram alerts for grid loss, grid restore, and battery low events.

**Stack:** Python 3, paho-mqtt, pymysql

### `solar-rule-engine/`
The decision engine. Runs every 5 minutes, reads live state from Solar Assistant MQTT, weather station (Ecowitt), and solar forecast (Open-Meteo), then issues commands to the inverter and controls smart switches (Shelly).

**Stack:** Python 3, paho-mqtt, requests, PyYAML

**Key features:**
- Pure `engine.evaluate()` function — fully unit-testable, no hardware required
- Hysteresis on inverter commands (prevents flip-flopping)
- Switch state tracker (min on/off time, daily run budget)
- `dry_run: true` default — logs what it would do without touching anything
- Ecowitt local or cloud source
- Open-Meteo GTI forecast with panel tilt/azimuth

## Architecture

```
Solar Assistant Pi (10.69.69.31)
  └── MQTT broker (port 1883)
        │
        ├──► mqtt_bridge     → MariaDB (telemetry + Telegram alerts)
        │
        └──► solar-rule-engine
               ├── sources/ecowitt.py    ← Ecowitt weather station
               ├── sources/openmeteo.py  ← Solar forecast
               ├── engine.py             ← Decision logic (pure)
               ├── switches/shelly.py    ← Shelly switch control
               └── mqtt_client.py        → inverter commands via SA MQTT
```

## Quick start

See [INSTALL.md](INSTALL.md) for full setup instructions.

```bash
git clone https://github.com/NeelsK/wattcast.git
cd wattcast/solar-rule-engine
cp config.yaml.example config.yaml
# Edit config.yaml with your site values
python -m pytest tests/ -v          # verify engine logic (no hardware needed)
python main.py --config config.yaml # run with dry_run: true first
```

## Requirements

- Raspberry Pi (tested on Pi 4, aarch64, Debian Bookworm)
- Solar Assistant running on a separate Pi with MQTT enabled
- Ecowitt weather station (local HTTP or cloud API)
- Shelly smart switches (Gen1 or Gen2)
- Python 3.10+
