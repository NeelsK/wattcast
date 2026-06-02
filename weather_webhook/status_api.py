#!/usr/bin/env python3
"""
WattCast Status API
===================
Lightweight Flask app that exposes a single /status endpoint returning
JSON with:
  - systemd service statuses (mqtt_bridge, weather_webhook, solar-rule-engine)
  - latest solar/inverter reading from weather_solar_readings
  - latest weather reading from weather_readings
  - geyser switch state (queried live from Shelly Gen2)

Runs on port 5001. Intended for local network use only.

Configuration via environment variables (set in status_api.service).
"""

import json
import os
import subprocess
import logging
from datetime import datetime, timezone

import pymysql
import pymysql.cursors
import requests as req_lib
from requests.auth import HTTPDigestAuth
import os as _os
from flask import Flask, jsonify, send_from_directory, request as flask_request

# ============================================================================
# CONFIG
# ============================================================================
DB_HOST  = os.getenv("DB_HOST",  "localhost")
DB_PORT  = int(os.getenv("DB_PORT", "3306"))
DB_NAME  = os.getenv("DB_NAME",  "wattcast")
DB_USER  = os.getenv("DB_USER",  "wattcast")
DB_PASS  = os.getenv("DB_PASS",  "")

GEYSER_IP       = os.getenv("GEYSER_IP",       "10.69.69.43")
GEYSER_PASS     = os.getenv("GEYSER_PASS",     "")
GEYSER_TIMEOUT  = int(os.getenv("GEYSER_TIMEOUT", "3"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PORT      = int(os.getenv("PORT", "5001"))

SERVICES = ["mqtt_bridge", "weather_webhook", "solar-rule-engine"]
ENGINE_STATE_FILE = os.getenv("ENGINE_STATE_FILE", "/tmp/wattcast_engine_state.json")

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("status_api")

app = Flask(__name__)

# ============================================================================
# HELPERS
# ============================================================================
def get_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        autocommit=True, connect_timeout=3,
    )


def service_status(name: str) -> dict:
    """Return systemd ActiveState and SubState for a service."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3,
        )
        active = result.stdout.strip()
        result2 = subprocess.run(
            ["systemctl", "show", name, "--property=ActiveState,SubState,ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=3,
        )
        props = {}
        for line in result2.stdout.strip().splitlines():
            k, _, v = line.partition("=")
            props[k] = v
        return {
            "active": active,
            "substate": props.get("SubState", ""),
            "since": props.get("ActiveEnterTimestamp", ""),
        }
    except Exception as e:
        return {"active": "error", "substate": str(e), "since": ""}


def latest_solar() -> dict | None:
    """Return most recent row from weather_solar_readings."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT timestamp, pv_power, battery_soc, battery_power,
                       grid_power, grid_connected, load_power,
                       generation_today, consumption_today
                FROM weather_solar_readings
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cur.fetchone()
        conn.close()
        if row:
            row["timestamp"] = str(row["timestamp"])
        return row
    except Exception as e:
        log.warning("latest_solar failed: %s", e)
        return None


def latest_weather() -> dict | None:
    """Return most recent row from weather_readings."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT timestamp, temp_outdoor, humidity_outdoor,
                       solar_radiation, uv_index, wind_speed, wind_gust,
                       rain_rate, rain_daily, pressure_relative
                FROM weather_readings
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cur.fetchone()
        conn.close()
        if row:
            row["timestamp"] = str(row["timestamp"])
        return row
    except Exception as e:
        log.warning("latest_weather failed: %s", e)
        return None


def geyser_state() -> dict:
    """Query Shelly Gen2 switch state via RPC."""
    try:
        url = f"http://{GEYSER_IP}/rpc/Switch.GetStatus"
        auth = HTTPDigestAuth("admin", GEYSER_PASS) if GEYSER_PASS else None
        r = req_lib.get(url, params={"id": 0}, auth=auth, timeout=GEYSER_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return {
            "reachable": True,
            "on": data.get("output", False),
            "power_w": data.get("apower"),
            "energy_kwh": round(data.get("aenergy", {}).get("total", 0) / 1000, 3),
            "temp_c": data.get("temperature", {}).get("tC"),
        }
    except Exception as e:
        log.warning("geyser_state failed: %s", e)
        return {"reachable": False, "error": str(e)}


def seconds_ago(ts_str: str) -> int | None:
    """Return how many seconds ago a 'YYYY-MM-DD HH:MM:SS' timestamp was."""
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        # Treat as local time (SAST, no tz info in DB)
        delta = datetime.now() - ts
        return int(delta.total_seconds())
    except Exception:
        return None


# ============================================================================
# ENDPOINTS
# ============================================================================
@app.route("/status", methods=["GET"])
def status():
    solar   = latest_solar()
    weather = latest_weather()
    geyser  = geyser_state()

    solar_age   = seconds_ago(solar["timestamp"])   if solar   else None
    weather_age = seconds_ago(weather["timestamp"]) if weather else None

    return jsonify({
        "ok": True,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "services": {name: service_status(name) for name in SERVICES},
        "solar": {
            "data": solar,
            "age_seconds": solar_age,
            "stale": solar_age is None or solar_age > 120,
        },
        "weather": {
            "data": weather,
            "age_seconds": weather_age,
            "stale": weather_age is None or weather_age > 600,
        },
        "geyser": geyser,
    })


@app.route("/engine", methods=["GET"])
def engine_state():
    try:
        with open(ENGINE_STATE_FILE) as f:
            data = json.load(f)
        return jsonify(data), 200
    except FileNotFoundError:
        return jsonify({"error": "No engine state yet — has the rule engine run?"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# PRESET API
# ============================================================================

def _preset_rows_to_dict(rows: list) -> dict:
    """Convert flat preset+slot DB rows into nested dict keyed by preset name."""
    presets: dict = {}
    for row in rows:
        name = row["name"]
        if name not in presets:
            presets[name] = {
                "name": name,
                "description": row.get("description", ""),
                "slots": [],
            }
        if row.get("slot_num") is not None:
            presets[name]["slots"].append({
                "slot_num":   int(row["slot_num"]),
                "time":       row["time"],
                "capacity":   int(row["capacity"]),
                "grid_charge": bool(row["grid_charge"]),
            })
    # Sort slots
    for p in presets.values():
        p["slots"].sort(key=lambda s: s["slot_num"])
    return presets


@app.route("/api/presets", methods=["GET"])
def api_get_presets():
    """Return all presets with their slots."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.name, p.description,
                       s.slot_num, s.time, s.capacity, s.grid_charge
                FROM engine_presets p
                LEFT JOIN engine_preset_slots s ON s.preset_id = p.id
                ORDER BY p.name, s.slot_num
            """)
            rows = cur.fetchall()
        conn.close()
        presets = _preset_rows_to_dict(rows)
        return jsonify({"presets": list(presets.values())}), 200
    except Exception as e:
        log.error("api_get_presets: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/presets/<name>", methods=["GET"])
def api_get_preset(name):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.name, p.description,
                       s.slot_num, s.time, s.capacity, s.grid_charge
                FROM engine_presets p
                LEFT JOIN engine_preset_slots s ON s.preset_id = p.id
                WHERE p.name = %s
                ORDER BY s.slot_num
            """, (name,))
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return jsonify({"error": "Preset not found"}), 404
        presets = _preset_rows_to_dict(rows)
        return jsonify({"preset": list(presets.values())[0]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/presets/<name>", methods=["PUT"])
def api_save_preset(name):
    """Create or update a preset and its 6 slots."""
    data = flask_request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    description = data.get("description", "")
    slots = data.get("slots", [])

    if len(slots) != 6:
        return jsonify({"error": "Must provide exactly 6 slots"}), 400

    # Validate capacity floors
    for s in slots:
        if int(s.get("capacity", 0)) < 40:
            return jsonify({"error": f"Slot {s.get('slot_num')} capacity below minimum 40%"}), 400

    try:
        conn = get_db()
        with conn.cursor() as cur:
            # Upsert preset
            cur.execute("""
                INSERT INTO engine_presets (name, description)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE description = VALUES(description), updated_at = CURRENT_TIMESTAMP
            """, (name, description))
            cur.execute("SELECT id FROM engine_presets WHERE name = %s", (name,))
            preset_id = cur.fetchone()["id"]

            # Delete existing slots and reinsert
            cur.execute("DELETE FROM engine_preset_slots WHERE preset_id = %s", (preset_id,))
            for s in slots:
                cur.execute("""
                    INSERT INTO engine_preset_slots (preset_id, slot_num, time, capacity, grid_charge)
                    VALUES (%s, %s, %s, %s, %s)
                """, (preset_id, int(s["slot_num"]), s["time"],
                      int(s["capacity"]), int(bool(s.get("grid_charge", True)))))
        conn.close()
        log.info("Preset '%s' saved to DB", name)
        return jsonify({"ok": True, "name": name}), 200
    except Exception as e:
        log.error("api_save_preset: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/presets/<name>", methods=["DELETE"])
def api_delete_preset(name):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM engine_presets WHERE name = %s", (name,))
            affected = cur.rowcount
        conn.close()
        if affected == 0:
            return jsonify({"error": "Preset not found"}), 404
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# UI PAGES
# ============================================================================

@app.route("/", methods=["GET"])
def dashboard():
    here = _os.path.dirname(_os.path.abspath(__file__))
    return send_from_directory(here, "status_api.html")


@app.route("/presets", methods=["GET"])
def page_presets():
    here = _os.path.dirname(_os.path.abspath(__file__))
    return send_from_directory(here, "presets.html")


@app.route("/health", methods=["GET"])
def health():
    try:
        conn = get_db()
        conn.close()
        return jsonify(status="ok", db="connected"), 200
    except Exception as e:
        return jsonify(status="error", db=str(e)), 500


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    log.info("Starting WattCast status API on port %d", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
