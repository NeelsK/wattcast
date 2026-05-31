"""
sources/openmeteo.py — Fetch solar forecast from Open-Meteo (free, no API key).

Uses global_tilted_irradiance (GTI) which accounts for panel tilt and azimuth,
so no manual decomposition math is needed.

Open-Meteo note: all radiation values are backwards averages over the preceding
hour. The value timestamped 08:00 covers 07:00–08:00.
"""

import logging
from datetime import datetime, timedelta, timezone
import zoneinfo

import requests

from models import ForecastSummary, HourlyForecast

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch(config: dict) -> ForecastSummary:
    """
    Fetch hourly forecast from Open-Meteo and return a ForecastSummary.

    config keys used:
        location.latitude, location.longitude, location.timezone
        panels.tilt, panels.azimuth, panels.total_kwp
        thresholds.forecast_good_kwh (used only for logging, not decision)
    """
    loc = config["location"]
    panels = config["panels"]
    tz = zoneinfo.ZoneInfo(loc["timezone"])

    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "timezone": loc["timezone"],
        "forecast_days": 2,
        "hourly": ",".join([
            "global_tilted_irradiance",     # W/m² on panel surface — key output
            "precipitation",
            "precipitation_probability",
            "cloud_cover",
            "temperature_2m",
        ]),
        # Tell Open-Meteo our panel geometry — it does the tilt/azimuth math
        "tilt": panels["tilt"],
        "azimuth": panels["azimuth"],
    }

    logger.debug("Fetching Open-Meteo forecast for %.4f, %.4f", loc["latitude"], loc["longitude"])

    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hourly = data["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in hourly["time"]]

    hours: list[HourlyForecast] = []
    for i, dt in enumerate(times):
        hours.append(HourlyForecast(
            hour=dt,
            gti_wm2=hourly["global_tilted_irradiance"][i] or 0.0,
            precipitation_mm=hourly["precipitation"][i] or 0.0,
            precipitation_probability=(hourly["precipitation_probability"][i] or 0) / 100.0,
            cloud_cover=(hourly["cloud_cover"][i] or 0) / 100.0,
            temperature=hourly["temperature_2m"][i] or 0.0,
        ))

    return _summarise(hours, panels["total_kwp"], tz)


def _summarise(hours: list[HourlyForecast], kwp: float, tz) -> ForecastSummary:
    """
    Derive today-remaining and tomorrow full-day yield estimates.

    Yield estimation:
        Each hourly GTI value (W/m²) × system efficiency × panel kWp / 1000
        gives kWh for that hour (since 1 hour × W = Wh → /1000 = kWh).

    System efficiency (~0.80) accounts for:
        - Inverter efficiency (~0.96)
        - Cable/connection losses (~0.98)
        - Temperature derating (~0.95)
        - Soiling/mismatch (~0.97)
        Combined: 0.96 × 0.98 × 0.95 × 0.97 ≈ 0.87 — use 0.80 conservatively.

    GTI is in W/m² averaged over the hour. For a 1 kWp system under 1000 W/m²
    standard test conditions, 1 hour = 1 kWh. So:
        yield_kwh = (gti_wm2 / 1000) × kwp × efficiency
    """
    EFFICIENCY = 0.80

    now = datetime.now(tz=tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    today_remaining: list[HourlyForecast] = [
        h for h in hours if h.hour.date() == today and h.hour > now
    ]
    tomorrow_hours: list[HourlyForecast] = [
        h for h in hours if h.hour.date() == tomorrow
    ]

    def yield_kwh(hour_list: list[HourlyForecast]) -> float:
        return sum((h.gti_wm2 / 1000) * kwp * EFFICIENCY for h in hour_list)

    def peak_gti(hour_list: list[HourlyForecast]) -> float:
        return max((h.gti_wm2 for h in hour_list), default=0.0)

    def max_rain_prob(hour_list: list[HourlyForecast]) -> float:
        return max((h.precipitation_probability for h in hour_list), default=0.0)

    def rain_hours(hour_list: list[HourlyForecast]) -> int:
        return sum(1 for h in hour_list if h.precipitation_mm > 0.5)

    summary = ForecastSummary(
        today_remaining_yield_kwh=yield_kwh(today_remaining),
        today_peak_gti=peak_gti(today_remaining),
        tomorrow_yield_kwh=yield_kwh(tomorrow_hours),
        tomorrow_peak_gti=peak_gti(tomorrow_hours),
        tomorrow_rain_probability=max_rain_prob(tomorrow_hours),
        tomorrow_rain_hours=rain_hours(tomorrow_hours),
    )

    logger.info(
        "Forecast: today remaining=%.1f kWh | tomorrow=%.1f kWh | rain prob=%.0f%% | rain hours=%d",
        summary.today_remaining_yield_kwh,
        summary.tomorrow_yield_kwh,
        summary.tomorrow_rain_probability * 100,
        summary.tomorrow_rain_hours,
    )

    return summary
