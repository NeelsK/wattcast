#!/usr/bin/env python3
"""
WattCast WeatherProvider
Fetches live irradiance from the local weather station and solar forecasts
from FarmWeather, then injects signals into the SignalSnapshot.

Signals produced:
  irradiance          — current solar radiation W/m² (from weather station)
  irradiance_30m_avg  — 30-minute rolling average W/m² (last 6 readings × 5min)
  pv_forecast_today   — remaining solar generation today kWh (calibrated)
  pv_forecast_tomorrow— tomorrow total solar generation kWh (calibrated)
  temp_outdoor        — current outdoor temperature °C
  rain_rate           — current rain rate mm/h

Config (environment variables or pass to constructor):
  WEATHER_HOST        — weather station hostname (default: weather.eschatologist.org)
  FARMWEATHER_KEY     — FarmWeather API key
  FARMWEATHER_REGION  — FarmWeather region slug (default: reitz)
  GTI_CALIBRATION     — calibration multiplier for FarmWeather GTI (default: 1.5)
  WEATHER_POLL_S      — live weather poll interval seconds (default: 300 = 5min)
  FORECAST_POLL_S     — forecast poll interval seconds (default: 21600 = 6h)
  SIGNAL_STALE_S      — seconds before a cached signal is treated as missing (default: 900)
"""

import os
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
import urllib.request
import urllib.error
import json

from rule_engine import Signal, SignalSnapshot, SignalSource

log = logging.getLogger("wattcast.weather")

# ── Config ────────────────────────────────────────────────────────────────────

WEATHER_HOST     = os.getenv("WEATHER_HOST",     "weather.eschatologist.org")
FARMWEATHER_KEY  = os.getenv("FARMWEATHER_KEY",  "fw_t73mqQHx61d10B2JAWnYOREiliGmOrOALWnARPqc")
FARMWEATHER_REGION = os.getenv("FARMWEATHER_REGION", "reitz")
GTI_CALIBRATION  = float(os.getenv("GTI_CALIBRATION", "1.5"))   # FarmWeather GTI underreports ~30-45%
WEATHER_POLL_S   = int(os.getenv("WEATHER_POLL_S",  "300"))      # 5 minutes
FORECAST_POLL_S  = int(os.getenv("FORECAST_POLL_S", "21600"))    # 6 hours
SIGNAL_STALE_S   = int(os.getenv("SIGNAL_STALE_S",  "900"))      # 15 minutes


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 10) -> Optional[dict | list]:
    """Fetch a URL and return parsed JSON, or None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WattCast/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except urllib.error.URLError as e:
        log.warning(f"Fetch failed {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse error {url}: {e}")
        return None
    except Exception as e:
        log.warning(f"Unexpected error fetching {url}: {e}")
        return None


# ── WeatherProvider ───────────────────────────────────────────────────────────

class WeatherProvider:
    """
    Runs two background threads:
      - live_loop: polls weather station every WEATHER_POLL_S seconds
      - forecast_loop: polls FarmWeather every FORECAST_POLL_S seconds

    Call inject(snapshot) to add weather signals to a SignalSnapshot.
    Signals older than SIGNAL_STALE_S are treated as unavailable (None).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, Signal] = {}
        self._running = False
        self._live_thread: Optional[threading.Thread] = None
        self._forecast_thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background polling threads."""
        self._running = True

        # Fetch immediately on start, then on schedule
        self._live_thread = threading.Thread(
            target=self._live_loop, name="weather-live", daemon=True)
        self._forecast_thread = threading.Thread(
            target=self._forecast_loop, name="weather-forecast", daemon=True)

        self._live_thread.start()
        self._forecast_thread.start()
        log.info(f"WeatherProvider started — live every {WEATHER_POLL_S}s, "
                 f"forecast every {FORECAST_POLL_S}s")

    def stop(self) -> None:
        self._running = False

    def inject(self, snapshot: SignalSnapshot) -> None:
        """Copy cached weather signals into the snapshot. Stale signals are skipped."""
        now = datetime.now()
        with self._lock:
            for key, signal in self._cache.items():
                if signal.timestamp is None:
                    continue
                age_s = (now - signal.timestamp).total_seconds()
                if age_s > SIGNAL_STALE_S:
                    log.debug(f"Signal '{key}' stale ({age_s:.0f}s old) — skipping")
                    continue
                snapshot.signals[key] = signal

    def status(self) -> dict:
        """Return a dict of current cached signal values for logging."""
        now = datetime.now()
        out = {}
        with self._lock:
            for key, signal in self._cache.items():
                age = (now - signal.timestamp).total_seconds() if signal.timestamp else None
                out[key] = {"value": signal.value, "age_s": round(age) if age else None}
        return out

    # ── Live weather loop ─────────────────────────────────────────────────────

    def _live_loop(self) -> None:
        while self._running:
            try:
                self._fetch_live()
            except Exception as e:
                log.error(f"Live weather fetch error: {e}")
            for _ in range(WEATHER_POLL_S):
                if not self._running:
                    return
                time.sleep(1)

    def _fetch_live(self) -> None:
        url = f"https://{WEATHER_HOST}/weather_api.php?action=current"
        data = _fetch_json(url)
        # Response is {"success": true, "data": [...]}
        if isinstance(data, dict):
            data = data.get("data", [])
        if not data or not isinstance(data, list) or len(data) == 0:
            log.warning("Live weather: empty or invalid response")
            return

        now = datetime.now()

        # Most recent reading is index 0
        latest = data[0]
        irradiance = self._parse_float(latest.get("solar_radiation"))
        temp       = self._parse_float(latest.get("temp_outdoor"))
        rain_rate  = self._parse_float(latest.get("rain_rate"))
        uv_index   = self._parse_float(latest.get("uv_index"))
        humidity   = self._parse_float(latest.get("humidity_outdoor"))
        wind_speed = self._parse_float(latest.get("wind_speed"))

        # 30-minute average irradiance — average of up to 6 readings (5min each)
        recent_readings = data[:6]
        irr_values = [
            self._parse_float(r.get("solar_radiation"))
            for r in recent_readings
            if self._parse_float(r.get("solar_radiation")) is not None
        ]
        irr_30m_avg = sum(irr_values) / len(irr_values) if irr_values else None

        with self._lock:
            def sig(key, value, unit=""):
                if value is not None:
                    self._cache[key] = Signal(
                        key=key, value=value, source=SignalSource.WEATHER,
                        unit=unit, timestamp=now)

            sig("irradiance",         irradiance,   "W/m²")
            sig("irradiance_30m_avg", irr_30m_avg,  "W/m²")
            sig("temp_outdoor",       temp,          "°C")
            sig("rain_rate",          rain_rate,     "mm/h")
            sig("uv_index",           uv_index,      "")
            sig("humidity_outdoor",   humidity,      "%")
            sig("wind_speed",         wind_speed,    "km/h")

        avg_str = f"{irr_30m_avg:.1f}" if irr_30m_avg is not None else "?"
        log.info(f"Live weather: irradiance={irradiance} W/m²  "
                 f"30m_avg={avg_str} W/m²  "
                 f"temp={temp}°C  rain={rain_rate}mm/h")

    # ── Forecast loop ─────────────────────────────────────────────────────────

    def _forecast_loop(self) -> None:
        while self._running:
            try:
                self._fetch_forecast()
            except Exception as e:
                log.error(f"Forecast fetch error: {e}")
            for _ in range(FORECAST_POLL_S):
                if not self._running:
                    return
                time.sleep(1)

    def _fetch_forecast(self) -> None:
        url = (f"https://farmweather.co.za/api/forecast/{FARMWEATHER_REGION}"
               f"?apikey={FARMWEATHER_KEY}&days=2")
        data = _fetch_json(url)
        if not data or "days" not in data or len(data["days"]) < 1:
            log.warning("Forecast: empty or invalid response")
            return

        now = datetime.now()
        days = data["days"]

        # Today's data
        today = days[0]
        today_gti_total = self._parse_float(today.get("solar_gti_kwh"))
        today_gti_cal   = (today_gti_total * GTI_CALIBRATION
                           if today_gti_total is not None else None)

        # Remaining solar today — sum hourly gti_wm2 for hours >= current hour
        # Convert W/m² hourly average to kWh: value × 1h / 1000
        current_hour = now.hour
        remaining_kwh = self._calc_remaining_kwh(today.get("hourly", []), current_hour)
        remaining_cal = (remaining_kwh * GTI_CALIBRATION
                         if remaining_kwh is not None else None)

        # Tomorrow's data
        tomorrow_gti_cal = None
        if len(days) >= 2:
            tomorrow = days[1]
            tomorrow_gti = self._parse_float(tomorrow.get("solar_gti_kwh"))
            tomorrow_gti_cal = (tomorrow_gti * GTI_CALIBRATION
                                if tomorrow_gti is not None else None)

        # Weather condition from today
        weather_condition = today.get("weather")    # e.g. "Fair", "Cloudy", "Rain"
        rain_probability  = self._parse_float(today.get("rain_probability"))
        temp_max          = self._parse_float(today.get("temp_max"))
        temp_min          = self._parse_float(today.get("temp_min"))

        with self._lock:
            def sig(key, value, source, unit=""):
                if value is not None:
                    self._cache[key] = Signal(
                        key=key, value=value, source=source,
                        unit=unit, timestamp=now)

            sig("pv_forecast_today",     today_gti_cal,    SignalSource.FORECAST, "kWh")
            sig("pv_forecast_remaining", remaining_cal,    SignalSource.FORECAST, "kWh")
            sig("pv_forecast_tomorrow",  tomorrow_gti_cal, SignalSource.FORECAST, "kWh")
            sig("weather_condition",     weather_condition, SignalSource.FORECAST, "")
            sig("rain_probability",      rain_probability,  SignalSource.FORECAST, "%")
            sig("temp_max_today",        temp_max,          SignalSource.FORECAST, "°C")
            sig("temp_min_today",        temp_min,          SignalSource.FORECAST, "°C")

        today_str     = f"{today_gti_cal:.1f}"     if today_gti_cal     is not None else "?"
        rem_str       = f"{remaining_cal:.1f}"     if remaining_cal     is not None else "?"
        tomorrow_str  = f"{tomorrow_gti_cal:.1f}"  if tomorrow_gti_cal  is not None else "?"
        log.info(f"Forecast: today={today_str}kWh (cal)  "
                 f"remaining={rem_str}kWh  "
                 f"tomorrow={tomorrow_str}kWh  "
                 f"condition={weather_condition}  rain_prob={rain_probability}%")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _calc_remaining_kwh(hourly: list, current_hour: int) -> Optional[float]:
        """
        Sum solar_gti_wm2 for hours >= current_hour.
        FarmWeather hourly times are "HH:00" strings.
        Each hour slot represents ~1 hour, so W/m² → kWh = value / 1000.
        """
        if not hourly:
            return None
        total = 0.0
        found = False
        for slot in hourly:
            try:
                slot_hour = int(slot["time"].split(":")[0])
            except (KeyError, ValueError, AttributeError):
                continue
            if slot_hour >= current_hour:
                gti = WeatherProvider._parse_float(slot.get("solar_gti_wm2"))
                if gti is not None:
                    total += gti / 1000.0   # W/m² × 1h → kWh
                    found = True
        return total if found else None


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    from rule_engine import SignalSnapshot

    provider = WeatherProvider()
    provider.start()

    print("\nFetching live + forecast data... (waiting 5s)\n")
    time.sleep(5)

    snap = SignalSnapshot()
    provider.inject(snap)

    print("=== Signals injected into snapshot ===")
    for key in sorted(snap.signals):
        s = snap.signals[key]
        print(f"  {key:30s} = {s.value}  {s.unit}")

    print(f"\nProvider cache status:")
    for key, info in provider.status().items():
        print(f"  {key:30s} = {info['value']}  (age {info['age_s']}s)")

    provider.stop()
