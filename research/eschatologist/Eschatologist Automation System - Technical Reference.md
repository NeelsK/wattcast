# Eschatologist Weather Station — Automation & Control System

**Technical Reference Document**
**System:** Reitz Weather Station (weather.eschatologist.org)
**Date:** May 2026

---

## 1. System Overview

The Eschatologist weather station in Reitz, Free State, South Africa runs an automation system that manages a 5kW Sunsynk hybrid inverter (with 15kWh battery bank) and a Shelly Plus 1PM-controlled geyser. The system makes decisions based on live weather data from an Ecowitt WS2910 station, solar production data from the Sunsynk cloud API, and ECMWF weather forecasts from FarmWeather.

Everything runs on Unraid via Docker (SWAG reverse proxy + MariaDB), with PHP handling all logic. There is no separate application server — the weather station's webhook, cron jobs, and web UI are all PHP scripts served by SWAG's built-in nginx/PHP-FPM.

### What the system controls

**Inverter (Sunsynk 5kW hybrid):** Applies named configuration presets that control grid charging schedules, battery SOC limits, time-of-use settings, and system work mode. Presets are pushed to the inverter via the Sunsynk cloud API.

**Geyser (via Shelly Plus 1PM):** Turns a WiFi relay switch on/off to divert solar surplus to the geyser. The Shelly also has its own built-in timer schedules as a fallback.

**Notifications (Telegram):** Sends alerts for grid outages, low battery, automation events, and station health issues.


---

## 2. Architecture — What Talks to What

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                                 │
├──────────────┬──────────────────┬───────────────────────────────────┤
│ Ecowitt      │ Sunsynk Cloud    │ FarmWeather                      │
│ WS2910       │ API              │ ECMWF Forecast API               │
│ (weather)    │ (solar/battery)  │ (forecast)                       │
└──────┬───────┴────────┬─────────┴──────────┬────────────────────────┘
       │                │                    │
       ▼                ▼                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     MARIADB DATABASE                                 │
│  weather_readings     weather_solar_readings    (cached forecasts)   │
│  weather_site_settings   inverter_presets   inverter_rules           │
│  weather_geyser_log   weather_notification_log                       │
└──────────┬───────────────────┬───────────────────┬───────────────────┘
           │                   │                   │
           ▼                   ▼                   ▼
┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────────┐
│ cron_automation  │  │ cron_cloudy_     │  │ weather_webhook.php     │
│ (every 2 min)   │  │ check (07:00)    │  │ (on each Ecowitt post)  │
│                  │  │                  │  │                         │
│ ┌──────────────┐│  │ Checks forecast  │  │ Stores weather +        │
│ │ Inverter     ││  │ solar hours.     │  │ solar readings.         │
│ │ Automation   ││  │ If cloudy, loads │  │                         │
│ │ Engine       ││  │ inverter preset. │  │ Checks grid/battery     │
│ │              ││  │                  │  │ thresholds for          │
│ │ Evaluates    ││  └────────┬─────────┘  │ Telegram notifications. │
│ │ rules in     ││           │            └────────────┬────────────┘
│ │ priority     ││           ▼                         │
│ │ order.       ││  ┌──────────────────┐               │
│ │ First match  ││  │ Sunsynk Cloud    │               │
│ │ wins.        ││  │ API              │               │
│ └──────┬───────┘│  │ (push preset)    │               │
│        │        │  └──────────────────┘               │
│ ┌──────▼───────┐│                                     │
│ │ Geyser Solar ││                                     │
│ │ Divert       ││                                     │
│ │              ││                                     │
│ │ Checks SOC + ││                                     │
│ │ solar rad.   ││                                     │
│ │ Toggles      ││                                     │
│ │ Shelly relay.││                                     │
│ └──────┬───────┘│                                     │
│        │        │                                     │
│ ┌──────▼───────┐│                                     │
│ │ Station      ││                                     │
│ │ Offline      ││                                     │
│ │ Check        ││                                     │
│ └──────────────┘│                                     │
└─────────────────┘                                     │
                                                        │
┌───────────────────────────────────────────────────────┘
│
▼
┌──────────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT API                                │
│  telegram_notify.php — cooldown/dedup via notification_log table     │
│  Events: grid_lost, grid_restored, battery_low, battery_recovered,  │
│          inverter_preset_loaded, cloudy_preset_loaded,               │
│          geyser_mode_changed, station_offline, shelly_offline        │
└──────────────────────────────────────────────────────────────────────┘
```


---

## 3. Inverter Automation Engine

### How it works

The inverter automation engine (`scripts/inverter_automation.php`) evaluates rules against live data and applies inverter preset configurations. It uses a **priority-based first-match-wins** model — rules are evaluated in priority order, and the first rule whose conditions are all met gets applied.

### Rule structure

Each rule has:

- **Name** — human-readable identifier
- **Priority** — lower number = evaluated first
- **Schedule** — either "daily at HH:MM" or "every N minutes"
- **Conditions** — JSON array of condition objects
- **Preset** — which inverter preset to load when conditions match
- **Enabled** — master toggle

### Condition format

Conditions are JSON arrays where each object specifies a field, operator, and value:

```json
[
  {"field": "battery_soc", "op": "<", "value": 30},
  {"field": "hour", "op": ">=", "value": 17},
  {"field": "solar_hours_remaining", "op": "<", "value": 2}
]
```

All conditions in a rule must be met (AND logic). Supported operators: `>`, `<`, `>=`, `<=`, `=`, `!=`.

### Available condition fields

The data provider (`InverterAutoDataProvider`) lazily fetches values only when referenced:

**Weather (from Ecowitt station):**
temp_current, humidity_current, wind_speed, wind_direction, pressure, rain_rate, solar_radiation, solar_radiation_1hr_avg, uv_index, dew_point, lightning_count, lightning_distance, soil_moisture

**Time:**
hour, minute, month, day_of_week, day_of_year

**Solar/Battery (from Sunsynk):**
battery_soc, pv_power, pv_power_1hr_avg, grid_power, load_power, battery_power, battery_power_1hr_avg, battery_hours_remaining, solar_generation_today

**ML Predictions:**
rain_prob_3hr, rain_prob_daily

**Forecast (from FarmWeather ECMWF):**
temp_min_tomorrow, temp_max_tomorrow, rain_forecast_tomorrow, weather_severity_today (0-6 scale), weather_severity_tomorrow, solar_hours_remaining, solar_hours_tomorrow, solar_gti_today, solar_gti_tomorrow, expected_gen_today, expected_gen_tomorrow

### Inverter presets

Presets are named configurations stored in the `inverter_presets` table. Each preset contains a JSON blob of inverter settings (grid charging times, SOC limits, work mode, etc.) that gets pushed to the Sunsynk cloud API via `SunsynkClient::updateInverterSettings()`.

Example preset settings include: system work mode, battery SOC limits per time slot, grid charge enable/disable, time-of-use schedules.

### Execution flow

1. Cron fires every 2 minutes → `cron_automation.php`
2. Loads all enabled rules ordered by priority
3. Filters to rules eligible to run now (based on schedule)
4. For each eligible rule, evaluates conditions against live data
5. First rule where ALL conditions are met → loads its preset
6. Preset settings pushed to Sunsynk API
7. Result logged to `inverter_rule_log` table
8. Telegram notification sent if configured

### Simulation mode

A global `inverter_automation_simulate` setting (default ON) makes the engine log what it *would* do without actually pushing settings to the inverter. This allows testing rules safely. The debug API endpoint (`run_automation`) always runs in simulation mode.


---

## 4. Geyser Solar Divert

### How it works

The geyser solar divert (`scripts/geyser_solar_divert.php`) runs every 2 minutes via the same cron as inverter automation. It controls a Shelly Plus 1PM WiFi relay to turn the geyser on/off based on solar conditions.

### Decision logic

The system uses **solar radiation from the weather station** (not PV power from the inverter) to determine if the sun is shining. This is because inverter PV output is demand-limited — when the battery is full and load is low, the inverter throttles PV even though plenty of sun is available. The weather station's solar radiation sensor gives the true, unthrottled reading.

**Turn ON when:**
- Battery SOC >= ON threshold (default 98%) AND
- Solar radiation >= minimum (default 650 W/m²)

**Stay ON (hysteresis) when:**
- Already on AND
- Battery SOC >= OFF threshold (default 90%) AND
- Solar radiation >= minimum

**Turn OFF when:**
- Battery SOC drops below OFF threshold, OR
- Solar radiation drops below minimum

The hysteresis gap (default 98% ON, 90% OFF) prevents the relay from flapping on/off rapidly when SOC hovers near a threshold.

### Three operating modes

The geyser has a three-way mode toggle:

**Auto** (default): Solar divert logic runs normally. Shelly timer schedules are enabled on the device. This is the hands-off mode.

**Force ON**: Relay is forced on immediately. Solar divert logic is skipped entirely. All Shelly timer schedules are disabled on the device so they don't override the forced state. Use for manual grid heating on demand.

**Force OFF**: Relay is forced off immediately. Solar divert logic is skipped. Shelly schedules disabled. Use when geyser is not needed or during maintenance.

Switching modes immediately toggles the relay and enables/disables Shelly schedules via the Gen2 RPC API.

### Shelly schedule management

The Shelly device has its own built-in timer schedules (e.g., "turn on at 09:00, off at 11:00") as a fallback if the automation system is offline. These are managed from the automation page UI:

- View/add/edit/delete schedules directly on the Shelly via Gen2 RPC API
- Save sets of schedules as named presets in the database
- Load a preset (replaces all current schedules on the device)
- Schedules use cron-like timespec format: `"SS MM HH DD MM DOW"`


---

## 5. Cloudy Day Morning Preset Loader

### How it works

A separate daily cron (`scripts/cron_cloudy_check.php`) runs at 07:00 each morning. It checks the FarmWeather ECMWF forecast for the day's expected solar hours (daylight hours with cloud cover < 50%). If solar hours fall below a configurable threshold, it loads a designated inverter preset to handle the cloudy day — typically one that enables grid charging and adjusts battery management for reduced solar.

### Configuration

Three settings (managed from the automation page UI):

- **Enabled** — master toggle
- **Solar hours threshold** — if forecast solar hours are below this, it's a "cloudy day" (default 3)
- **Inverter preset** — which preset to load on cloudy days (dropdown of existing presets)

### How solar hours are counted

The script counts hourly forecast entries that fall within daylight hours (sunrise to sunset, approximated by month for Reitz's latitude) where cloud cover is below 50%. Nighttime hours are excluded to avoid inflating the count.


---

## 6. Telegram Notifications

### How it works

`telegram_notify.php` is a shared helper included by webhook, cron scripts, and the API. Each notification has:

- **Event type** — identifies the kind of event
- **Cooldown period** — prevents duplicate alerts (e.g., don't re-send "grid lost" every 2 minutes while it's still down)
- **Per-event toggle** — each event type can be individually enabled/disabled
- **Threshold** (where applicable) — e.g., battery low at 20%, station offline after 15 minutes

### Events and cooldowns

| Event | Cooldown | Triggered from |
|-------|----------|----------------|
| grid_lost | 5 min | webhook (solar data save) |
| grid_restored | 1 min | webhook (solar data save) |
| battery_low | 10 min | webhook (solar data save) |
| battery_recovered | 1 min | webhook (solar data save) |
| inverter_preset_loaded | 1 min | inverter_automation.php |
| cloudy_preset_loaded | 1 min | cron_cloudy_check.php |
| geyser_mode_changed | 10 sec | debug API (mode change endpoint) |
| station_offline | 15 min | cron_automation.php (every 2 min) |
| shelly_offline | 10 min | geyser_solar_divert.php |

### Deduplication

Every notification attempt is logged to `weather_notification_log` with timestamp and success/failure. Before sending, the system checks if the same event type was successfully sent within the cooldown period. This prevents notification storms during extended events (long grid outage, sustained low battery, etc.).


---

## 7. Configuration Storage

All settings are stored in the `weather_site_settings` key-value table using an upsert pattern (`INSERT ... ON DUPLICATE KEY UPDATE`). This includes:

**Geyser settings:**
shelly_geyser_ip, shelly_geyser_enabled, geyser_soc_on, geyser_soc_off, geyser_pv_min, geyser_solar_rad_min, geyser_mode

**Cloudy day settings:**
geyser_cloudy_enabled, geyser_cloudy_min_solar_hrs, geyser_cloudy_preset_id

**Telegram settings:**
telegram_enabled, telegram_bot_token, telegram_chat_id, plus per-event notify_* toggles and thresholds

**Inverter automation:**
inverter_automation_enabled, inverter_automation_simulate


---

## 8. Database Tables

### Core automation tables

**inverter_presets** — Named inverter configurations. Fields: id, name, description, settings (JSON), created_at, updated_at.

**inverter_rules** — Automation rules. Fields: id, name, description, priority, enabled, schedule_type (daily/interval), schedule_time, schedule_interval, conditions (JSON), preset_id (FK), last_triggered_at, created_at, updated_at.

**inverter_rule_log** — Execution history. Fields: id, rule_id, rule_name, preset_name, result (applied/simulated/no_match/error/already_active), message, conditions_snapshot (JSON), data_snapshot (JSON), executed_at.

**geyser_schedule_presets** — Named sets of Shelly timer schedules. Fields: id, name, description, schedules (JSON), created_at.

**weather_geyser_log** — Geyser action history. Fields: id, timestamp, action, geyser_on, battery_soc, pv_power, grid_power, battery_power, reason.

**weather_notification_log** — Telegram notification history for dedup/cooldown. Fields: id, event_type, message, sent_at, telegram_ok, error_msg.

**weather_site_settings** — Key-value configuration store. Fields: setting_key (PK), setting_value.


---

## 9. Cron Schedule

| Schedule | Command | Purpose |
|----------|---------|---------|
| Every 2 min | `docker exec swag php /config/www/weather_web/scripts/cron_automation.php` | Inverter rules + geyser divert + station offline check |
| Daily 07:00 | `docker exec swag php /config/www/weather_web/scripts/cron_cloudy_check.php` | Cloudy day preset loader |

The 2-minute cron uses a lock file (`/tmp/cron_automation.lock`) to prevent overlapping runs. Stale locks older than 2 minutes are automatically cleaned up.


---

## 10. External APIs

**Sunsynk Cloud API** (`sunsynk_client.php`): RSA-encrypted OAuth authentication. Used to read inverter status and push preset settings. Plant ID 296661, Inverter SN 2303292601. File-based token caching.

**Shelly Gen2 RPC API**: Local HTTP calls to the Shelly device on the LAN. Used for Switch.Set (on/off), Switch.GetStatus, Schedule.List/Create/Update/Delete. 5-second timeout.

**FarmWeather Forecast API**: ECMWF forecast data for Reitz. Cached locally for 1 hour. Provides hourly cloud cover, temperature, precipitation, solar GTI, and wind data for today and tomorrow.

**Telegram Bot API**: Standard sendMessage endpoint with HTML parse mode. Bot token and chat ID stored in database.


---

## 11. File Reference

| File | Purpose |
|------|---------|
| `scripts/cron_automation.php` | Unified 2-minute cron runner |
| `scripts/inverter_automation.php` | Rule evaluation engine |
| `scripts/geyser_solar_divert.php` | Geyser on/off logic + Shelly RPC functions |
| `scripts/cron_cloudy_check.php` | Daily cloudy day preset loader |
| `telegram_notify.php` | Notification helper with cooldown/dedup |
| `sunsynk_client.php` | Sunsynk API client (OAuth, settings push) |
| `farmweather_forecast.php` | FarmWeather forecast client (cached) |
| `weather_webhook.php` | Ecowitt data receiver + solar data fetch + notification triggers |
| `weather_debug_api.php` | API endpoints for all settings, presets, rules, schedules, notifications |
| `weather_automation.php` | Automation page UI (geyser control, inverter rules, schedules) |
| `weather_debug.php` | Debug/settings page UI (notification config, forwarding, API keys) |
| `config.php` | Database connection, constants, station coordinates |


---

## 12. Design Decisions and Trade-offs

**Priority-based first-match-wins for inverter rules:** Chosen because inverter presets are mutually exclusive — you can only be in one configuration state at a time. An IFTTT-style "multiple rules fire independently" model would create conflicts. When more device types are added (lights, plugs), the architecture may evolve to support independent rule chains per device.

**Solar radiation instead of PV power for geyser decisions:** The inverter throttles PV output when demand is low (battery full, load minimal), reporting e.g. 500W when the panels could produce 3000W. The weather station's solar radiation sensor is unthrottled and reflects actual sunshine. This prevents the geyser from staying off on sunny days just because the inverter isn't drawing power.

**Separate cloudy day cron instead of inline logic:** The original design checked forecast solar hours within the main divert loop and ran the geyser from grid if conditions were met. This was replaced with a simpler approach: a daily morning cron that loads an inverter preset for the whole day. The inverter preset handles grid charging, battery management, and everything else — cleaner than trying to micro-manage individual settings from the geyser script.

**Cooldown-based dedup for notifications:** Rather than tracking state transitions explicitly (which requires knowing previous state), the system uses a simple time-based cooldown. If "grid_lost" was sent successfully in the last 5 minutes, don't send another one. This is simpler and handles edge cases like rapid flapping naturally.

**All config in database, not files:** Everything is in `weather_site_settings` rather than `.env` or config files. This allows the web UI to read/write settings without file system access, and keeps everything in one place. The Telegram bot token is in the DB — acceptable for a private, single-user system behind authentication.
