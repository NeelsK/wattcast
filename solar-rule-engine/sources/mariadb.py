"""
sources/mariadb.py — Fetch current weather from local MariaDB.

Reads the most recent row from weather_readings (written by the
weather_webhook receiver every ~300s from the Ecowitt station).

Returns a WeatherNow dataclass, same as ecowitt.py and openmeteo.py,
so the engine doesn't care which source is configured.
"""

import logging
from datetime import datetime, timedelta

import pymysql
import pymysql.cursors

from models import WeatherNow

logger = logging.getLogger(__name__)

# Maximum age of a weather reading before it's considered stale (seconds).
# Ecowitt uploads every 300s; allow 2× for missed readings.
MAX_AGE_SECONDS = 600


def fetch(config: dict) -> WeatherNow:
    """
    Fetch latest weather from local MariaDB.

    Config keys (under config["mariadb"]):
      host     — default localhost
      port     — default 3306
      database — default wattcast
      user     — default wattcast
      password — required
    """
    cfg = config.get("mariadb", {})

    conn = pymysql.connect(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port", 3306)),
        database=cfg.get("database", "wattcast"),
        user=cfg.get("user", "wattcast"),
        password=cfg.get("password", ""),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    solar_radiation,
                    temp_outdoor,
                    humidity_outdoor,
                    wind_speed,
                    rain_rate,
                    timestamp
                FROM weather_readings
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        logger.warning("No weather readings in local DB — returning zeros (Ecowitt not yet connected?)")
        return WeatherNow(irradiance=0.0, temperature=20.0, humidity=50.0, wind_speed=0.0, rain_rate=0.0)

    age = (datetime.now() - row["timestamp"]).total_seconds()
    if age > MAX_AGE_SECONDS:
        logger.warning(
            "Weather reading is %.0fs old (max %ds) — station may be offline",
            age, MAX_AGE_SECONDS,
        )

    logger.debug(
        "Weather from DB: solar=%.0f W/m²  temp=%.1f°C  rain=%.1f mm/h  age=%.0fs",
        row["solar_radiation"] or 0,
        row["temp_outdoor"] or 0,
        row["rain_rate"] or 0,
        age,
    )

    return WeatherNow(
        irradiance=float(row["solar_radiation"] or 0),
        temperature=float(row["temp_outdoor"] or 0),
        humidity=float(row["humidity_outdoor"] or 0),
        wind_speed=float(row["wind_speed"] or 0),
        rain_rate=float(row["rain_rate"] or 0),
    )
