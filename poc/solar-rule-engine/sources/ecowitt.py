"""
sources/ecowitt.py — Fetch current weather station readings from Ecowitt.

Supports two modes, selected by config.ecowitt.source:
  "local"  — HTTP GET to station IP on LAN (no key needed)
  "cloud"  — Ecowitt cloud REST API (requires app_key + api_key + mac)

Both paths return the same normalised WeatherNow dataclass.
"""

import logging
from datetime import datetime

import requests

from models import WeatherNow

logger = logging.getLogger(__name__)

# Ecowitt cloud API
CLOUD_BASE = "https://api.ecowitt.net/api/v3"


def fetch(config: dict) -> WeatherNow:
    """Dispatch to local or cloud based on config."""
    source = config["ecowitt"].get("source", "local")
    if source == "cloud":
        return _fetch_cloud(config["ecowitt"])
    return _fetch_local(config["ecowitt"])


# ---------------------------------------------------------------------------
# Local station fetch
# Ecowitt GW series gateway exposes a local HTTP API on port 80.
# Endpoint: GET http://<ip>/get_livedata_info
# Returns JSON with nested sensor data.
# ---------------------------------------------------------------------------

def _fetch_local(cfg: dict) -> WeatherNow:
    ip = cfg["station_ip"]
    url = f"http://{ip}/get_livedata_info"
    logger.debug("Fetching Ecowitt local: %s", url)

    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    # The local API groups sensors into lists keyed by type.
    # We fish out what we need defensively.
    common = data.get("common_list", [])
    solar = data.get("solar_and_uvi", [])
    rainfall = data.get("rain", [])

    def find(items: list, id_: str) -> float:
        for item in items:
            if item.get("id") == id_:
                return float(item.get("val", 0.0))
        return 0.0

    # Common sensor IDs (Ecowitt WS3800 / WH65 / similar):
    # 0x02 = outdoor temp (°C), 0x07 = outdoor humidity (%)
    # 0x0B = wind speed (m/s), 0x0C = gust speed
    # Solar: id "0x17" = solar radiation W/m²
    # Rain: id "0x0D" = rain rate mm/hr

    temp = find(common, "0x02")
    humidity = find(common, "0x07")
    wind_speed = find(common, "0x0B")
    irradiance = find(solar, "0x17")
    rain_rate = find(rainfall, "0x0D")

    return WeatherNow(
        irradiance=irradiance,
        temperature=temp,
        humidity=humidity,
        wind_speed=wind_speed,
        rain_rate=rain_rate,
    )


# ---------------------------------------------------------------------------
# Cloud API fetch
# Ecowitt cloud: https://doc.ecowitt.net/web/#/apiv3
# GET /api/v3/device/real_time
# ---------------------------------------------------------------------------

def _fetch_cloud(cfg: dict) -> WeatherNow:
    url = f"{CLOUD_BASE}/device/real_time"
    params = {
        "application_key": cfg["app_key"],
        "api_key": cfg["api_key"],
        "mac": cfg["mac_address"],
        "call_back": "all",
        "temp_unitid": 1,       # Celsius
        "pressure_unitid": 3,   # hPa
        "wind_speed_unitid": 7, # m/s
        "rainfall_unitid": 12,  # mm
        "solar_irradiance_unitid": 16,  # W/m²
    }
    logger.debug("Fetching Ecowitt cloud for MAC %s", cfg.get("mac_address", "?"))

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"Ecowitt cloud API error: {data.get('msg', 'unknown')}")

    sensors = data.get("data", {})

    def get_val(path: list[str]) -> float:
        """Navigate nested dict safely."""
        node = sensors
        for key in path:
            if not isinstance(node, dict):
                return 0.0
            node = node.get(key, {})
        return float(node.get("value", 0.0)) if isinstance(node, dict) else 0.0

    return WeatherNow(
        irradiance=get_val(["solar_and_uvi", "solar"]),
        temperature=get_val(["outdoor", "temperature"]),
        humidity=get_val(["outdoor", "humidity"]),
        wind_speed=get_val(["wind", "wind_speed"]),
        rain_rate=get_val(["rainfall", "rain_rate"]),
    )
