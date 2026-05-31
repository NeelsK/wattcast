# Solar Assistant Pi — Internal Architecture & Integration Findings

**Investigated via SSH:** 2026-05-27  
**Device:** Raspberry Pi 5, solar-assistant user  
**SA Version:** 2026-03-24  

---

## Process & Runtime

SA runs as a compiled Elixir/BEAM release called `influx-bridge`:
- Binary: `/usr/lib/influx-bridge/influx-bridge.v2026-03-24` (18MB, compiled — not readable source)
- Symlink: `/usr/lib/influx-bridge/influx-bridge → influx-bridge.v2026-03-24`
- Loaded into RAM at `/dev/shm/grafana-sync/<hash>/` at startup (root-owned, not accessible)
- Systemd service: `/etc/systemd/system/solar-assistant.service` (alias: `influx-bridge`)
- Updates via versioned binary swap — WattCast must never depend on SA internals

The app is a black box. Inverter protocol definitions are compiled in, not readable config files.

---

## Inverter Brands Supported (from UI dropdown)

Solar Assistant supports the following inverter brands out of the box:

- Afore
- **Deye, SunSynk, Sol-Ark** ← installer's primary brand
- Felicity Solar
- **GoodWe**
- **Growatt**
- Huawei
- **Luxpower**
- Megarevo
- MidNite Solar
- Must
- SAJ
- SRNE
- Senergy
- Sigenergy
- **Solis**
- Sumry
- **Sungrow**
- **Victron**
- **Voltronic, Axpert, MPP Solar, Infini** ← very common in SA

This covers essentially the entire SA residential solar market.

## BMS/Battery Protocols Supported

- Emulated BMS
- USB Daly UART/RS485
- USB JBD RS485
- USB JK RS485
- USB Modbus RS232/485
- USB Narada RS485
- USB PylonTech/Pytes console
- USB Serial RS232/485
- USB Voltronic LIB RS485
- USB Victron VE.Direct
- USB CAN bus

---

## Network Services

| Port | Service | Bind | Auth | Notes |
|---|---|---|---|---|
| 80 | SA Web UI + REST API | 0.0.0.0 | Password (set via UI) | Full REST + WebSocket API |
| 1883 | MQTT broker (Mosquitto) | 0.0.0.0 | **None** (allow_anonymous true) | Must be enabled in SA UI first |
| 8086 | InfluxDB HTTP API | 127.0.0.1 | **None** | Localhost only — direct query access |
| 8088 | InfluxDB RPC | 127.0.0.1 | **None** | Localhost only — backup/restore |

**MQTT config** (`/etc/mosquitto/mosquitto.conf`):
```
listener 1883 0.0.0.0
allow_anonymous true
persistence false
```

---

## InfluxDB — Direct Data Access

InfluxDB runs on localhost:8086 with no authentication. All SA metrics are stored here.

**Database:** `solar_assistant`  
**Retention policy:** `autogen` — duration `0s` (data kept forever — monitor SD card space)

### Query examples
```bash
# All measurements
curl "http://localhost:8086/query?db=solar_assistant&q=SHOW+MEASUREMENTS"

# Latest values
curl "http://localhost:8086/query?db=solar_assistant&q=SELECT+last(*)+FROM+%22Battery+state+of+charge%22"
```

### Full measurement list
- AC output voltage
- Battery current, power, power hourly, power in/out hourly, SoC, temperature, voltage
- Cloud cover
- Generator power
- Grid frequency, power, power hourly, power in/out hourly, voltage
- Inverter temperature
- Load power, load power essential/non-essential, load power hourly
- Outside temperature
- PV current 1/2, PV power, PV power 1/2, PV power hourly, PV power predicted/predicted hourly
- PV voltage 1/2

### Data structure
- `combined` field = total across all inverters
- `inverter_0`, `inverter_1`... = per-inverter values
- No tag keys — flat measurement model
- Timestamps in UTC

**Sample data observed (2026-05-27, CAN port test):**
- Battery SoC: 96%
- Battery voltage: 49.6V
- Load power: 481W
- Grid power: 20W
- Inverter temperature: 40.2°C
- PV power: ~1W (noise, not connected)

---

## MQTT Topic Structure

Topics observed when broker enabled:
```
solar_assistant/total/battery_energy_in/state
solar_assistant/total/battery_energy_out/state
solar_assistant/total/grid_energy_in/state
solar_assistant/total/grid_energy_out/state
solar_assistant/total/load_energy/state
solar_assistant/total/pv_energy/state
```

Pattern: `solar_assistant/<device>/<measurement>/state`
- `total` = combined totals
- `inverter_1` = per-inverter values (expected once live data flows)

Write settings: `solar_assistant/<device>/set/<setting>` (must enable "Allow setting changes" in SA MQTT config)

**Note:** Only 6 retained energy total messages observed — inverter not physically connected yet (awaiting RS485 cable splitter). Full topic stream will be visible once connected.

---

## Root Access Findings (2026-05-28)

Root access confirmed via `sudo su -`. This unlocked everything previously inaccessible.

### Mnesia Database — SA Settings Store

SA stores all configuration in Erlang Mnesia DB at `/usr/lib/influx-bridge/Mnesia.nonode@nohost/`.

Files:
- `Elixir.Database.DAT` — top-level schema (6KB)
- `Elixir.Database.Setting.DAT` — all site/inverter settings (17KB)
- `Elixir.Database.Setting_3.DAT` — older setting table version (9KB)
- `Elixir.Database.UserToken*.DAT` — auth tokens (multiple files)
- `schema.DAT` — Mnesia schema

**Setting keys extracted from `Setting.DAT`** (these are the SA internal config keys):

Site/system settings:
- `site.id`, `site.host` (nkr.za.solar-assistant.io), `site.theme`, `site.update_until`, `site.last_perpetual_id`, `site.activation_hash`
- `site_owner.email` (neelskriekext@pm.me), `site_owner.password_hash`
- `eula_accepted` = true
- `update.channel` = stable
- `timezone`, `locale`, `time_format` = 24-hour, `date_ordering`, `temperature_unit`
- `auto_start_devices` = true
- `system.scheduled_reboot`

Location settings:
- `location.name` = Reitz
- `location.country_code`, `location.latitude`, `location.longitude`
- `ip.latitude`, `ip.longitude`, `ip.country_code`

Display settings:
- `display.battery_metric` = both
- `display.max_battery_power`, `display.max_pv_power`, `display.max_grid_power`
- `display.solar_prediction_widget`

Inverter settings:
- `inverter.count` = 1
- `inverter.driver_id`
- `inverter.model_id`
- `inverter.port_ids` = `1027_24577_BG03O7KY` (USB device ID)
- `inverter.interface_id` = rs485
- `inverter.allow_passive_reading`
- `inverter.sunsynk.grid_connection`

BMS settings:
- `bms.driver_id` = inverter (using inverter BMS, not separate)
- `bms.model_id` = inverter
- `bms.port_ids`

PV/solar model settings:
- `pv.rated_power`
- `pv.azimuth`, `pv.tilt`, `pv.nmot`, `pv.temp_coeff`

MQTT settings:
- `mqtt.publish` = false (was disabled, enabled during testing)
- `mqtt.prefix`, `mqtt.allow_update`, `mqtt.reset_energy`

WiFi settings:
- `wifi.ssid`, `wifi.password`, `wifi.country_code`

Automation:
- `automation.list` (appears 3 times — this is where SA automation rules are stored as a list)

### Provisioning Directory

`/dev/shm/.../priv/provisioning/` contains SA's system provisioning files:

**bin/**
- `influx-bridge-setup` — setup binary (68KB)
- `opi3lts/update_boot.sh` — Orange Pi 3 LTS boot update script

**files/** — config files deployed to the OS:
- `mosquitto.conf` — MQTT broker config
- `influxdb.conf` — InfluxDB config (21KB — full config)
- `influxdb.list` — apt source for InfluxDB
- `auto-hotspot` + `auto-hotspot.service` — WiFi/hotspot management script
- `bt-ip-forwarding.service` — Bluetooth IP forwarding
- `masqdns` + `masqdns.service` — DNS masquerading
- `pan0.network` — PAN network interface config
- `opi3lts/boot/overlay/` — Orange Pi device tree overlays (SPI, fixup)
- `opi3lts/boot/sun50i-h6-orangepi-3-lts.dtb` — Orange Pi device tree blob

**Key insight:** SA supports Orange Pi 3 LTS (`opi3lts`) in addition to Raspberry Pi — this is a cheaper ARM board (~R200) SA uses for their own hardware offering.

### Inverter BEAM Modules — Complete List

All compiled inverter drivers found in `/dev/shm/.../ebin/`:

**Afore:** Driver, Model.Default  
**Felicity:** Driver, Model.IVEM, Model.TRex, Model.TRexHV  
**GoodWe:** Driver (+ A5Connection, A5.Request, A5.Response, Network variants), Model.DT, Model.Hybrid, Model.Hybrid3Phase, Model.HybridSplitPhase, Model.Legacy  
**Growatt:** Driver, Model.Common, Model.SPA, Model.SPF, Model.SPH, Model.SPH3, Model.SPHHU, Model.TLX, Model.TLXH, Model.WIT  
**Huawei:** Driver, Model.Sun2000  
**Luxpower:** Driver (+ Network, TCPCommand, TCPConnection, TCPResponse, TCPSimulation), Model.ACS, Model.Common, Model.GridBoss, Model.LXP, Model.SNA  
**Megarevo:** Driver, Model.Hybrid  
**MidNite:** Driver (+ Connection, Request, Response), Model.Default  
**Must:** Driver, Model.EP3300, Model.EP3300TLV, Model.EPCommon, Model.PH10, Model.PH11, Model.PH18  
**SAJ:** Driver, Model.All  
**SRNE:** Driver, Model.Common, Model.Default, Model.SinglePhase, Model.SplitPhase, Model.ThreePhase  
**Senergy:** Driver, Model.Default  
**Sigenergy:** Driver, Model.Default  
**Solarman:** Driver.Command, Driver.Connection, Driver.Discovery, Driver.Response ← WiFi dongle protocol (used by Deye/SunSynk)  
**Solis:** Driver, Model.Hybrid, Model.String  
**Sumry:** Driver, Model.HybridV1–V4, Model.OffGrid  
**Sungrow:** Driver, Model.Common, Model.GridTied, Model.Hybrid  
**SunPower:** Driver (+ Response), Model.Default  
**SunSynk:** Driver, Model.Common, Model.Hybrid, Model.Hybrid3Phase, Model.Hybrid3PhaseHV, Model.String  
**Victron:** Driver, Model.Default  
**Voltronic:** Driver (+ Command, Connection, ConnectionV2, Response, Simulation), Metric, Model.Axpert, Model.AxpertHybrid, Model.AxpertKing, Model.AxpertKing35, Model.AxpertMax, Model.AxpertMKS, Model.AxpertMKS42, Model.AxpertMultiphase, Model.AxpertPlus, Model.AxpertVM, Model.FiveStar, Model.InfiniSolar, Model.InfiniSolarMultiphase, Model.InfiniSolarV, Model.InfiniSolarVMultiphase, Model.InfiniSolarWPMultiphase

**Generic:** `Elixir.SolarAssistant.Inverter.Setting` — the shared setting definition used by all inverters

**SunSynk note:** Has both a `Driver` (RS485/Modbus) and `Solarman.Driver` (WiFi dongle) — two connection paths for the same inverter family.

### Gettext / UI String Keys (Afrikaans .po sample)

SA ships full Afrikaans localisation. Useful UI string keys observed:
- `Driver`, `Miscellaneous settings`, `Monitoring connection`
- `Battery`, `Dashboard`, `Grid`, `Inverter`, `Inverter cluster`, `Load`, `Solar PV`
- `Alarms`, `Charts`, `Configuration`
- `Day ahead prices`, `Planned grid outages` ← SA has loadshedding schedule awareness built into UI
- `Power`, `Premium`, `Price`, `Source`

---

## What We Cannot Access

- `mosquitto_sub`/`mosquitto_pub` — not installed on SA Pi (use from dev machine)
- `paho-mqtt` Python package — not installed on SA Pi

**Workaround for MQTT from dev machine:** `mosquitto_sub -h 10.69.69.31 -p 1883 -t '#' -v`

---

## Key Takeaways for WattCast

1. **SA handles all inverter protocol complexity** — covers every brand the installer uses
2. **InfluxDB is the richest data source** but localhost-only — only accessible if WattCast runs on same Pi
3. **MQTT is the cleanest LAN integration** — open, no auth, accessible from any device on LAN
4. **SA REST API** is the most structured option — requires auth, works over LAN
5. **Do NOT install WattCast software on the SA Pi** — SA updates via binary swap, no stability guarantees
6. **Separate WattCast Pi is the right approach** — talks to SA via MQTT over LAN
