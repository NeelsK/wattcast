#!/usr/bin/env python3
"""
MQTT Bridge — Solar Assistant → MariaDB
========================================
Runs on the WattCast Pi. Subscribes to solar_assistant/#,
debounces to one DB row per minute, and handles Telegram grid/battery
notifications by calling the same logic the PHP webhook used to handle.
This service replaces storeSolarReading() in weather_webhook.php and is
the stepping stone toward full Pi-side automation.

Configuration: set environment variables (see mqtt_bridge.service), or edit defaults below.

Install dependencies:
    pip3 install paho-mqtt pymysql requests

Run:
    python3 mqtt_bridge.py

Run as systemd service: see mqtt_bridge.service alongside this file.
"""
import os
import time
import logging
import threading
import signal
import sys
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
import pymysql
import pymysql.cursors
import requests

# ============================================================================
# CONFIG — override with environment variables (preferred) or edit defaults
# ============================================================================
MQTT_HOST      = os.getenv("MQTT_HOST",     "10.69.69.31")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER      = os.getenv("MQTT_USER",     "")   # leave blank if no auth
MQTT_PASS      = os.getenv("MQTT_PASS",     "")
DB_HOST        = os.getenv("DB_HOST",       "10.69.69.4")   # Unraid host IP
DB_PORT        = int(os.getenv("DB_PORT",   "3306"))
DB_NAME        = os.getenv("DB_NAME",       "weather_data")
DB_USER        = os.getenv("DB_USER",       "weather_user")
DB_PASS        = os.getenv("DB_PASS",       "")             # set in service file, never hardcode
# How often to flush a row to the DB (seconds). 60 = one row per minute.
FLUSH_INTERVAL = int(os.getenv("FLUSH_INTERVAL", "60"))
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO")

# ============================================================================
# TOPIC → state dict key mapping
# ============================================================================
# Maps each MQTT topic suffix to the key we store in our in-memory state dict.
# Only topics listed here are processed; everything else is silently ignored.
TOPIC_MAP = {
    # Real-time power (from inverter_1 — more granular than total/*)
    "inverter_1/pv_power/state":                    "pv_power",
    "inverter_1/pv_power_1/state":                  "pv1_power",
    "inverter_1/pv_power_2/state":                  "pv2_power",
    "inverter_1/load_power/state":                  "load_power",
    "inverter_1/grid_power/state":                  "grid_power",
    "inverter_1/grid_voltage/state":                "grid_voltage",   # used to derive grid_connected
    "inverter_1/ac_output_voltage/state":           "ac_voltage",
    "inverter_1/ac_output_frequency/state":         "ac_frequency",
    # Battery (total/battery_power gives signed aggregate)
    "total/battery_power/state":                    "battery_power",
    "total/battery_state_of_charge/state":          "battery_soc",
    # Daily energy counters (reset at midnight by Solar Assistant)
    "total/pv_energy/state":                        "generation_today",
    "total/load_energy/state":                      "consumption_today",
    "total/battery_energy_in/state":                "battery_charge_today",
    "total/battery_energy_out/state":               "battery_discharge_today",
    "total/grid_energy_in/state":                   "grid_import_today",
    "total/grid_energy_out/state":                  "grid_export_today",
}

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mqtt_bridge")

# ============================================================================
# STATE
# ============================================================================
_state: dict = {}          # latest values keyed by TOPIC_MAP values
_state_lock = threading.Lock()
_last_flush: float = 0.0   # epoch of last successful DB write
_running = True

# ============================================================================
# MQTT CALLBACKS
# ============================================================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to SA MQTT at %s:%d", MQTT_HOST, MQTT_PORT)
        client.subscribe("solar_assistant/#")
        log.info("Subscribed to solar_assistant/#")
    else:
        log.error("MQTT connection failed, rc=%d", rc)

def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning("MQTT disconnected unexpectedly (rc=%d) — will auto-reconnect", rc)

def on_message(client, userdata, msg):
    topic = msg.topic
    # Strip the solar_assistant/ prefix
    suffix = topic[len("solar_assistant/"):] if topic.startswith("solar_assistant/") else topic
    if suffix not in TOPIC_MAP:
        return
    key = TOPIC_MAP[suffix]
    try:
        value = float(msg.payload.decode().strip())
    except (ValueError, UnicodeDecodeError):
        return  # non-numeric topic (e.g. device_mode string) — skip
    with _state_lock:
        _state[key] = value

# ============================================================================
# DATABASE HELPERS
# ============================================================================
def get_db() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
    )

def load_notification_config(cursor) -> dict:
    cursor.execute("""
        SELECT setting_key, setting_value
        FROM weather_site_settings
        WHERE setting_key IN (
            'telegram_enabled', 'telegram_bot_token', 'telegram_chat_id',
            'notify_grid_lost', 'notify_grid_restored',
            'notify_battery_low', 'notify_battery_low_threshold',
            'notify_battery_recovered', 'notify_battery_recovered_threshold'
        )
    """)
    rows = cursor.fetchall()
    cfg = {r["setting_key"]: r["setting_value"] for r in rows}
    return {
        "telegram_enabled":                   bool(int(cfg.get("telegram_enabled", "0"))),
        "telegram_bot_token":                 cfg.get("telegram_bot_token", ""),
        "telegram_chat_id":                   cfg.get("telegram_chat_id", ""),
        "notify_grid_lost":                   bool(int(cfg.get("notify_grid_lost", "1"))),
        "notify_grid_restored":               bool(int(cfg.get("notify_grid_restored", "1"))),
        "notify_battery_low":                 bool(int(cfg.get("notify_battery_low", "1"))),
        "notify_battery_low_threshold":       int(cfg.get("notify_battery_low_threshold", "20")),
        "notify_battery_recovered":           bool(int(cfg.get("notify_battery_recovered", "1"))),
        "notify_battery_recovered_threshold": int(cfg.get("notify_battery_recovered_threshold", "50")),
    }

def is_in_cooldown(cursor, event_type: str, cooldown_secs: int) -> bool:
    cursor.execute("""
        SELECT sent_at FROM weather_notification_log
        WHERE event_type = %s
        ORDER BY sent_at DESC LIMIT 1
    """, (event_type,))
    row = cursor.fetchone()
    if not row:
        return False
    age = (datetime.now() - row["sent_at"]).total_seconds()
    return age < cooldown_secs

COOLDOWNS = {
    "grid_lost":         300,
    "grid_restored":      60,
    "battery_low":       600,
    "battery_recovered":  60,
}

def send_telegram(token: str, chat_id: str, message: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.json().get("ok", False)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False

def notify(cursor, cfg: dict, event_type: str, message: str):
    if not cfg["telegram_enabled"]:
        return
    if not cfg["telegram_bot_token"] or not cfg["telegram_chat_id"]:
        return
    cooldown = COOLDOWNS.get(event_type, 300)
    if is_in_cooldown(cursor, event_type, cooldown):
        return
    ok = send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], message)
    cursor.execute("""
        INSERT INTO weather_notification_log (event_type, message, sent_at, telegram_ok)
        VALUES (%s, %s, NOW(), %s)
    """, (event_type, message, 1 if ok else 0))
    log.info("Telegram [%s]: %s", event_type, "sent" if ok else "failed")

def check_notifications(cursor, cfg: dict, grid_connected: bool, battery_soc: int):
    """Mirror the PHP checkGridNotification + checkBatteryNotification logic."""
    cursor.execute("""
        SELECT grid_connected, battery_soc
        FROM weather_solar_readings
        WHERE grid_connected IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1 OFFSET 1
    """)
    prev = cursor.fetchone()
    if not prev:
        return
    was_connected = bool(prev["grid_connected"])
    prev_soc      = int(prev["battery_soc"] or 0)

    # Grid transitions
    if cfg["notify_grid_lost"] and was_connected and not grid_connected:
        notify(cursor, cfg, "grid_lost",
               f"⚡ <b>Grid power lost!</b>\nBattery: {battery_soc}%\n"
               "System running on solar + battery")
    if cfg["notify_grid_restored"] and not was_connected and grid_connected:
        notify(cursor, cfg, "grid_restored",
               f"✅ <b>Grid power restored</b>\nBattery: {battery_soc}%")

    # Battery thresholds
    low  = cfg["notify_battery_low_threshold"]
    high = cfg["notify_battery_recovered_threshold"]
    if cfg["notify_battery_low"] and prev_soc >= low and battery_soc < low:
        notify(cursor, cfg, "battery_low",
               f"🔋 <b>Battery low!</b>\nSOC dropped to <b>{battery_soc}%</b> "
               f"(threshold: {low}%)")
    if cfg["notify_battery_recovered"] and prev_soc < high and battery_soc >= high:
        notify(cursor, cfg, "battery_recovered",
               f"🔋 <b>Battery recovered</b>\nSOC back to <b>{battery_soc}%</b>")

# ============================================================================
# FLUSH TO DB
# ============================================================================
def flush(snapshot: dict):
    """Write one row to weather_solar_readings from the current state snapshot."""
    if not snapshot:
        return
    grid_voltage   = snapshot.get("grid_voltage", 0.0)
    grid_connected = 1 if grid_voltage > 10.0 else 0
    battery_soc    = int(snapshot.get("battery_soc", 0))
    now            = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO weather_solar_readings
                (timestamp, pv_power, battery_power, grid_power, grid_connected,
                 load_power, battery_soc, ac_voltage, ac_frequency,
                 pv1_power, pv2_power,
                 generation_today, consumption_today,
                 battery_charge_today, battery_discharge_today,
                 grid_import_today, grid_export_today)
            VALUES
                (%s, %s, %s, %s, %s,
                 %s, %s, %s, %s,
                 %s, %s,
                 %s, %s,
                 %s, %s,
                 %s, %s)
        """, (
            now,
            snapshot.get("pv_power"),
            snapshot.get("battery_power"),
            snapshot.get("grid_power"),
            grid_connected,
            snapshot.get("load_power"),
            battery_soc,
            snapshot.get("ac_voltage"),
            snapshot.get("ac_frequency"),
            snapshot.get("pv1_power"),
            snapshot.get("pv2_power"),
            snapshot.get("generation_today"),
            snapshot.get("consumption_today"),
            snapshot.get("battery_charge_today"),
            snapshot.get("battery_discharge_today"),
            snapshot.get("grid_import_today"),
            snapshot.get("grid_export_today"),
        ))
        log.debug(
            "DB write: SoC=%s%%  PV=%sW  Load=%sW  Grid=%sW  grid_conn=%s",
            battery_soc,
            snapshot.get("pv_power"),
            snapshot.get("load_power"),
            snapshot.get("grid_power"),
            grid_connected,
        )
        # Telegram notifications (reads previous row, so must run after INSERT)
        try:
            cfg = load_notification_config(cursor)
            check_notifications(cursor, cfg, bool(grid_connected), battery_soc)
        except Exception as e:
            log.warning("Notification check failed: %s", e)
        cursor.close()
        conn.close()
    except pymysql.Error as e:
        log.error("DB write failed: %s", e)

# ============================================================================
# FLUSH LOOP (runs in background thread)
# ============================================================================
def flush_loop():
    global _last_flush
    while _running:
        time.sleep(1)
        now = time.time()
        if now - _last_flush >= FLUSH_INTERVAL:
            with _state_lock:
                snapshot = dict(_state)
            if snapshot:
                flush(snapshot)
                _last_flush = now

# ============================================================================
# MAIN
# ============================================================================
def shutdown(signum, frame):
    global _running
    log.info("Shutting down")
    _running = False
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    log.info("Starting MQTT bridge  (flush interval: %ds)", FLUSH_INTERVAL)

    t = threading.Thread(target=flush_loop, daemon=True)
    t.start()

    client = mqtt.Client()
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()

if __name__ == "__main__":
    main()
