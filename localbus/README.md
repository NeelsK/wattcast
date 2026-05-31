# LocalBus

LocalBus is the WattCast hardware abstraction layer for local inverter communication on the Pi edge device. It provides a single, brand-agnostic interface that the rule engine uses to read inverter telemetry and apply configuration presets — regardless of which inverter is installed.

```
WattCast Rule Engine
        │
        ▼
  InverterDriver   ←— single interface, defined in localbus/base.py
        │
   ┌────┴─────┐
   │          │
Sunsynk    Victron    (future: Deye, GoodWe, ...)
Modbus     VE.Bus
Driver     Driver
   │
RS485 / USB adapter
   │
Sunsynk 5K-SG04LP1
```

## Structure

```
localbus/
├── __init__.py               # Driver registry + create_driver() factory
├── base.py                   # InverterDriver abstract base class + signal key constants
├── README.md                 # This file
└── drivers/
    ├── __init__.py
    ├── sunsynk_modbus.py     # Sunsynk RS485 driver (SunsynkModbusDriver)
    └── sunsynk_registers.py  # Sunsynk register map (addresses, scales, codecs)
```

## How the rule engine uses LocalBus

The Pi agent creates a driver once at startup using `create_driver()`, then on every 2-minute cycle:

```python
from localbus import create_driver

driver = create_driver(
    brand="sunsynk_modbus",
    plant_id="eschatologist",
    port="/dev/sunsynk",
)

with driver:
    # 1. Read live telemetry — feeds the rule engine's Context Signals
    snapshot = driver.read_snapshot()
    # → {"battery_soc": 87, "pv1_power": 2340, "grid_voltage": 232.1, ...}

    # 2. Apply a preset when a rule matches
    result = driver.apply_preset({
        "work_mode": 1,           # "Zero Export to Load"
        "battery_min_soc": 20,
        "tou_time_1": 2200,       # 22:00
        "tou_soc_1": 90,
    })
    # → PresetResult(success=True, applied=[...], failed=[])

    # 3. Dry-run mode — rule simulation before going live
    result = driver.apply_preset(preset_settings, dry_run=True)
    # → logs what would be written, no hardware writes

    # 4. Diagnostics
    driver.ping()               # → True/False
    driver.read_register("battery_min_soc")  # → 20
```

## Canonical signal keys

All drivers return data keyed by the canonical signal names defined in `localbus/base.py`. These are the names the rule engine uses in conditions:

| Key | Unit | Description |
|---|---|---|
| `battery_soc` | % | State of charge |
| `battery_voltage` | V | DC voltage |
| `battery_current` | A | Positive = charging |
| `battery_power` | W | Positive = charging |
| `battery_temp` | °C | Battery temperature |
| `pv1_power` / `pv2_power` | W | Solar array output |
| `grid_voltage` | V | AC grid voltage |
| `grid_power` | W | Positive = importing |
| `load_power` | W | Total consumption |
| `inverter_temp` | °C | Heatsink temperature |
| `work_mode` | str | Current work mode label |
| `day_pv_energy` | kWh | Today's solar generation |
| `day_grid_import` / `day_grid_export` | kWh | Today's grid exchange |
| `day_load_energy` | kWh | Today's consumption |

Writable keys (appear in Preset blobs):

| Key | Description |
|---|---|
| `work_mode` | 0–3 or string label |
| `battery_min_soc` | % minimum before stop discharge |
| `battery_max_charge_current` | A |
| `battery_max_discharge_current` | A |
| `tou_time_1` … `tou_time_6` | HHMM (e.g. 530 = 05:30) |
| `tou_soc_1` … `tou_soc_6` | % target SOC per TOU slot |

## Adding a new inverter brand

1. Create `localbus/drivers/<brand>_<method>.py` with a class that subclasses `InverterDriver` from `localbus/base.py`.
2. Implement the five abstract methods: `connect()`, `disconnect()`, `read_snapshot()`, `apply_preset()`, `read_register()`, `ping()`.
3. Map the brand's native register/field names to canonical signal keys in `read_snapshot()`.
4. Register the driver in `localbus/__init__.py`:
   ```python
   def _load_deye_modbus():
       from localbus.drivers.deye_modbus import DeyeModbusDriver
       return DeyeModbusDriver

   DRIVER_REGISTRY["deye_modbus"] = _load_deye_modbus
   ```
5. The plant config's `inverter.local_driver` field uses this key.

The rule engine, preset model, and execution log need no changes.

## Sunsynk-specific notes

- Default port: `/dev/sunsynk` (stable udev symlink). Falls back to `/dev/ttyUSB0`.
- Baud rate: 9600. Slave address: 1. These are Sunsynk defaults.
- Register addresses can shift between firmware versions. Before enabling live writes, run `read_register()` on each control register and confirm the value matches the inverter LCD.
- Temperature registers sometimes report `(raw - 1000) / 10` on older firmware. If `battery_temp` reads ~1023°C, edit the scale/offset in `sunsynk_registers.py`.
- The Sunsynk Connect research folder (`/WattCast/research/Sunsynk Connect/`) has hardware wiring diagrams, Pi setup guide, and example Modbus commands.
