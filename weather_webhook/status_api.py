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
from flask import Flask, jsonify, send_from_directory

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


@app.route("/", methods=["GET"])
def dashboard():
    here = _os.path.dirname(_os.path.abspath(__file__))
    return send_from_directory(here, "status_api.html")


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
