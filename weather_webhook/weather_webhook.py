#!/usr/bin/env python3
"""
WattCast Weather Webhook Receiver
===================================
Receives Ecowitt push data (HTTP POST, WU protocol format).
1. Validates and stores to local MariaDB
2. Forwards raw POST to upstream Unraid webhook

Configuration via environment variables (set in weather_webhook.service).

Ecowitt device setup:
  Protocol: Ecowitt or WeatherUnderground
  Server IP: <this Pi's IP>
  Path: /weather
  Port: 8080
  Upload interval: 300s
"""

import os
import logging
import math
import threading
import requests as req_lib
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
import pymysql
import pymysql.cursors

# ============================================================================
# CONFIG
# ============================================================================
DB_HOST     = os.getenv("DB_HOST",     "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_NAME     = os.getenv("DB_NAME",     "wattcast")
DB_USER     = os.getenv("DB_USER",     "wattcast")
DB_PASS     = os.getenv("DB_PASS",     "")          # set in service file
TIMEZONE    = os.getenv("TIMEZONE",    "Africa/Johannesburg")
TZ_OFFSET   = int(os.getenv("TZ_OFFSET_HOURS", "2"))  # SAST = UTC+2

UPSTREAM_URL     = os.getenv("UPSTREAM_URL", "http://weather.eschatologist.org/weather_webhook.php")
UPSTREAM_ENABLED = os.getenv("UPSTREAM_ENABLED", "true").lower() == "true"
UPSTREAM_TIMEOUT = int(os.getenv("UPSTREAM_TIMEOUT", "10"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PORT      = int(os.getenv("PORT", "8080"))

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weather_webhook")

# ============================================================================
# FLASK APP
# ============================================================================
app = Flask(__name__)

# ============================================================================
# ECOWITT FIELD MAPPING (same as PHP webhook)
# Ecowitt sends imperial units; we convert to metric for storage.
# ============================================================================
FIELD_MAP = {
    "tempf":           ("temp_outdoor",       "temp_f"),
    "temp1f":          ("temp_outdoor",       "temp_f"),
    "tempinf":         ("temp_indoor",        "temp_f"),
    "indoortempf":     ("temp_indoor",        "temp_f"),
    "humidity":        ("humidity_outdoor",   "none"),
    "humidity1":       ("humidity_outdoor",   "none"),
    "humidityin":      ("humidity_indoor",    "none"),
    "indoorhumidity":  ("humidity_indoor",    "none"),
    "baromabsin":      ("pressure_absolute",  "inhg"),
    "baromrelin":      ("pressure_relative",  "inhg"),
    "windspeedmph":    ("wind_speed",         "mph"),
    "windgustmph":     ("wind_gust",          "mph"),
    "winddir":         ("wind_direction",     "none"),
    "rainratein":      ("rain_rate",          "inches"),
    "eventrainin":     ("rain_event",         "inches"),
    "hourlyrainin":    ("rain_hourly",        "inches"),
    "dailyrainin":     ("rain_daily",         "inches"),
    "weeklyrainin":    ("rain_weekly",        "inches"),
    "monthlyrainin":   ("rain_monthly",       "inches"),
    "uv":              ("uv_index",           "none"),
    "solarradiation":  ("solar_radiation",    "none"),
    "lightning_num":   ("lightning_count",    "none"),
    "lightning":       ("lightning_distance", "none"),
    "soilmoisture1":   ("soil_moisture_1",    "none"),
    "soilmoisture2":   ("soil_moisture_2",    "none"),
    "wh57batt":        ("batt_lightning",     "none"),
    "soilbatt1":       ("batt_soil_1",        "none"),
    "wh80batt":        ("batt_wind",          "none"),
}

BOUNDS = {
    "temp_outdoor":      (-40,  65),
    "temp_indoor":       (-10,  60),
    "humidity_outdoor":  (  0, 100),
    "humidity_indoor":   (  0, 100),
    "pressure_absolute": (800, 1100),
    "pressure_relative": (900, 1100),
    "wind_speed":        (  0, 250),
    "wind_gust":         (  0, 300),
    "wind_direction":    (  0, 360),
    "solar_radiation":   (  0, 2000),
    "uv_index":          (  0,  20),
    "lightning_count":   (  0, 10000),
    "lightning_distance":(  0, 100),
    "soil_moisture_1":   (  0, 100),
    "soil_moisture_2":   (  0, 100),
}

# ============================================================================
# UNIT CONVERSION
# ============================================================================
def convert(value: float, unit: str) -> float:
    if unit == "temp_f":
        return (value - 32) * 5 / 9
    if unit == "inhg":
        return value * 33.8639
    if unit == "mph":
        return value * 1.60934
    if unit == "inches":
        return value * 25.4
    return value

# ============================================================================
# DERIVED VALUES
# ============================================================================
def dew_point(temp_c: float, humidity: float) -> float:
    a, b = 17.27, 237.7
    alpha = (a * temp_c) / (b + temp_c) + math.log(humidity / 100)
    return (b * alpha) / (a - alpha)

def heat_index(temp_c: float, humidity: float) -> float:
    tf = temp_c * 9 / 5 + 32
    if tf < 80:
        return temp_c
    hi = (-42.379 + 2.04901523 * tf + 10.14333127 * humidity
          - 0.22475541 * tf * humidity - 0.00683783 * tf ** 2
          - 0.05481717 * humidity ** 2 + 0.00122874 * tf ** 2 * humidity
          + 0.00085282 * tf * humidity ** 2
          - 0.00000199 * tf ** 2 * humidity ** 2)
    return (hi - 32) * 5 / 9

def wind_chill(temp_c: float, wind_kmh: float) -> float:
    if temp_c > 10 or wind_kmh < 4.8:
        return temp_c
    return (13.12 + 0.6215 * temp_c
            - 11.37 * wind_kmh ** 0.16
            + 0.3965 * temp_c * wind_kmh ** 0.16)

# ============================================================================
# DATABASE
# ============================================================================
def get_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        autocommit=True, connect_timeout=5,
    )

def store_reading(weather: dict, timestamp: str, raw: dict):
    fields = list(weather.keys()) + ["raw_data"]
    placeholders = ", ".join(["%s"] * (len(fields) + 1))  # +1 for timestamp
    col_list = ", ".join(fields)
    sql = f"INSERT INTO weather_readings (timestamp, {col_list}) VALUES (%s, {placeholders[3:]})"
    # Rebuild properly
    cols = "timestamp, " + col_list
    vals_ph = ", ".join(["%s"] * (1 + len(fields)))
    sql = f"INSERT INTO weather_readings ({cols}) VALUES ({vals_ph})"
    values = [timestamp] + list(weather.values()) + [str(raw)]

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, values)
            reading_id = conn.insert_id()
            update_daily_summary(cur, timestamp[:10])
        return reading_id
    finally:
        conn.close()

def update_daily_summary(cur, date: str):
    cur.execute("""
        INSERT INTO weather_daily_summary (
            date,
            temp_min, temp_max, temp_avg,
            humidity_min, humidity_max, humidity_avg,
            pressure_min, pressure_max, pressure_avg,
            wind_speed_max, wind_gust_max, wind_speed_avg,
            rain_total, uv_max, solar_max, solar_avg,
            dew_point_avg, reading_count
        )
        SELECT
            DATE(timestamp),
            MIN(temp_outdoor), MAX(temp_outdoor), AVG(temp_outdoor),
            MIN(humidity_outdoor), MAX(humidity_outdoor), AVG(humidity_outdoor),
            MIN(pressure_relative), MAX(pressure_relative), AVG(pressure_relative),
            MAX(wind_speed), MAX(wind_gust), AVG(wind_speed),
            (SELECT wr2.rain_daily FROM weather_readings wr2
             WHERE DATE(wr2.timestamp) = %s
             ORDER BY wr2.timestamp DESC LIMIT 1),
            MAX(uv_index), MAX(solar_radiation), AVG(solar_radiation),
            AVG(dew_point), COUNT(*)
        FROM weather_readings
        WHERE DATE(timestamp) = %s
        GROUP BY DATE(timestamp)
        ON DUPLICATE KEY UPDATE
            temp_min = VALUES(temp_min), temp_max = VALUES(temp_max), temp_avg = VALUES(temp_avg),
            humidity_min = VALUES(humidity_min), humidity_max = VALUES(humidity_max),
            humidity_avg = VALUES(humidity_avg),
            pressure_min = VALUES(pressure_min), pressure_max = VALUES(pressure_max),
            pressure_avg = VALUES(pressure_avg),
            wind_speed_max = VALUES(wind_speed_max), wind_gust_max = VALUES(wind_gust_max),
            wind_speed_avg = VALUES(wind_speed_avg),
            rain_total = VALUES(rain_total),
            uv_max = VALUES(uv_max), solar_max = VALUES(solar_max), solar_avg = VALUES(solar_avg),
            dew_point_avg = VALUES(dew_point_avg),
            reading_count = VALUES(reading_count),
            updated_at = CURRENT_TIMESTAMP
    """, (date, date))

# ============================================================================
# UPSTREAM FORWARDING (runs in background thread — never blocks response)
# ============================================================================
def forward_upstream(data: dict):
    if not UPSTREAM_ENABLED:
        return
    def _send():
        try:
            r = req_lib.post(UPSTREAM_URL, data=data, timeout=UPSTREAM_TIMEOUT)
            log.debug("Upstream forward: %s %s", r.status_code, r.text[:100])
        except Exception as e:
            log.warning("Upstream forward failed: %s", e)
    threading.Thread(target=_send, daemon=True).start()

# ============================================================================
# WEBHOOK ENDPOINT
# ============================================================================
@app.route("/weather", methods=["POST"])
def weather():
    data = request.form.to_dict()
    if not data:
        return jsonify(success=False, message="No data"), 400

    log.debug("Received: %s", data)

    # Parse timestamp
    if "dateutc" in data:
        try:
            ts = datetime.strptime(data["dateutc"], "%Y-%m-%d %H:%M:%S")
            ts = ts + timedelta(hours=TZ_OFFSET)  # convert UTC → local
        except ValueError:
            ts = datetime.now()
    else:
        ts = datetime.now()
    timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")

    # Map and convert fields
    weather = {}
    seen = set()
    for ecowitt_key, (db_field, unit) in FIELD_MAP.items():
        if ecowitt_key in data and data[ecowitt_key] != "" and db_field not in seen:
            try:
                val = convert(float(data[ecowitt_key]), unit)
                weather[db_field] = round(val, 4)
                seen.add(db_field)
            except ValueError:
                pass

    if not weather:
        return jsonify(success=False, message="No valid fields"), 400

    # Bounds check
    anomalies = []
    for field, (lo, hi) in BOUNDS.items():
        if field in weather and not (lo <= weather[field] <= hi):
            anomalies.append(f"{field}={weather[field]:.2f} (expected {lo}–{hi})")
    if anomalies:
        log.warning("Anomaly rejected: %s", anomalies)
        return jsonify(success=False, message="Anomaly: " + ", ".join(anomalies)), 422

    # Derived values
    if "temp_outdoor" in weather and "humidity_outdoor" in weather:
        weather["dew_point"] = round(dew_point(weather["temp_outdoor"], weather["humidity_outdoor"]), 2)
        weather["heat_index"] = round(heat_index(weather["temp_outdoor"], weather["humidity_outdoor"]), 2)
    if "temp_outdoor" in weather and "wind_speed" in weather:
        weather["wind_chill"] = round(wind_chill(weather["temp_outdoor"], weather["wind_speed"]), 2)

    # Lightning timestamp
    if data.get("lightning_time") and int(data.get("lightning_time", 0)) > 0:
        lt = datetime.fromtimestamp(int(data["lightning_time"]))
        weather["lightning_time"] = lt.strftime("%Y-%m-%d %H:%M:%S")

    # Strip passkey from raw storage
    raw = {k: v for k, v in data.items() if k.upper() != "PASSKEY"}

    # Store locally
    try:
        store_reading(weather, timestamp, raw)
        log.info("Stored: SoC=n/a  Solar=%.0fW/m²  Temp=%.1f°C  Rain=%.1fmm/h",
                 weather.get("solar_radiation", 0),
                 weather.get("temp_outdoor", 0),
                 weather.get("rain_rate", 0))
    except Exception as e:
        log.error("DB store failed: %s", e)
        # Still forward upstream even if local store fails
        forward_upstream(data)
        return jsonify(success=False, message=str(e)), 500

    # Forward to Unraid (non-blocking)
    forward_upstream(data)

    return jsonify(success=True, message="OK"), 200


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
    log.info("Starting weather webhook on port %d", PORT)
    log.info("Upstream forwarding: %s → %s", "enabled" if UPSTREAM_ENABLED else "disabled", UPSTREAM_URL)
    app.run(host="0.0.0.0", port=PORT, debug=False)
