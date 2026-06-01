"""
main.py — Entry point for the WattCast solar rule engine.

Wires data sources, rule engine, and MQTT together.

Run with:
    python main.py --config config.yaml

Flow each cycle:
  1. Fetch weather (MariaDB or fallback)
  2. Refresh Open-Meteo forecast if stale
  3. Get live system state from Solar Assistant MQTT
  4. Evaluate rules → select preset
  5. If preset changed AND cooldown elapsed → apply preset via MQTT
  6. Write engine state JSON for status dashboard
"""

import argparse
import json
import logging
import os
import signal
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

import yaml

import sources.openmeteo as openmeteo
from engine import evaluate, load_presets
from models import EvalResult, ForecastSummary
from mqtt_client import SolarAssistantMQTT


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

STATE_FILE   = os.getenv("ENGINE_STATE_FILE", "/tmp/wattcast_engine_state.json")
HISTORY_MAX  = 20


def write_engine_state(
    last_eval: EvalResult,
    active_preset: Optional[str],
    last_switch_time: Optional[datetime],
    history: deque,
    dry_run: bool,
) -> None:
    def _ser(v):
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return v

    entry = {
        "timestamp":        last_eval.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "matched_rule":     last_eval.matched_rule,
        "matched_preset":   last_eval.matched_preset,
        "reason":           last_eval.reason,
        "preset_applied":   last_eval.preset_applied,
        "suppressed":       last_eval.suppressed,
        "preset_unchanged": last_eval.preset_unchanged,
        "inputs":           {k: round(v, 2) for k, v in last_eval.inputs.items()},
    }
    payload = {
        "last_eval":        entry,
        "active_preset":    active_preset,
        "last_switch_time": last_switch_time.strftime("%Y-%m-%d %H:%M:%S") if last_switch_time else None,
        "dry_run":          dry_run,
        "history":          list(history),
    }
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, default=_ser)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logging.getLogger("main").warning("Failed to write engine state: %s", e)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="WattCast Solar Rule Engine")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    engine_cfg = config["engine"]
    setup_logging(engine_cfg.get("log_level", "INFO"))

    logger = logging.getLogger("main")

    dry_run         = engine_cfg.get("dry_run", True)
    eval_interval   = engine_cfg.get("eval_interval_seconds", 300)
    forecast_refresh_td = timedelta(minutes=engine_cfg.get("forecast_refresh_minutes", 60))
    cooldown_td     = timedelta(minutes=engine_cfg.get("preset_cooldown_minutes", 30))

    if dry_run:
        logger.warning("DRY RUN MODE — preset changes will be logged but NOT sent to inverter")

    # Validate presets at startup
    presets = load_presets(config)
    logger.info("Loaded %d presets: %s", len(presets), list(presets.keys()))

    mqtt = SolarAssistantMQTT(config)

    forecast: Optional[ForecastSummary] = None
    forecast_fetched_at: Optional[datetime] = None

    active_preset:    Optional[str]      = None
    last_switch_time: Optional[datetime] = None
    eval_history: deque = deque(maxlen=HISTORY_MAX)

    # Graceful shutdown
    running = True
    def _shutdown(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    mqtt.connect()
    logger.info("Waiting 10s for initial MQTT readings from Solar Assistant...")
    time.sleep(10)

    logger.info("Starting rule engine loop (interval: %ds, cooldown: %dm)",
                eval_interval, cooldown_td.seconds // 60)

    while running:
        loop_start = datetime.now()

        try:
            # 1. Refresh forecast if stale
            if forecast is None or (datetime.now() - forecast_fetched_at) > forecast_refresh_td:
                logger.info("Refreshing Open-Meteo forecast...")
                try:
                    forecast = openmeteo.fetch(config)
                    forecast_fetched_at = datetime.now()
                except Exception as e:
                    logger.error("Forecast fetch failed: %s", e)
                    if forecast is None:
                        logger.warning("No forecast available — skipping cycle")
                        time.sleep(eval_interval)
                        continue

            # 2. Fetch weather
            weather_source = config.get("weather_source", "mariadb")
            try:
                if weather_source == "mariadb":
                    import sources.mariadb as mariadb_src
                    weather = mariadb_src.fetch(config)
                elif weather_source == "openmeteo":
                    from models import WeatherNow
                    weather = WeatherNow(irradiance=0, temperature=20, humidity=50,
                                        wind_speed=0, rain_rate=0)
                else:
                    import sources.ecowitt as ecowitt_src
                    weather = ecowitt_src.fetch(config)
            except Exception as e:
                logger.error("Weather fetch failed: %s", e)
                time.sleep(eval_interval)
                continue

            # Publish weather to MQTT / HA
            mqtt.publish_weather(weather, forecast)

            # 3. Get system state
            system = mqtt.get_system_state()
            if system is None:
                logger.warning("No system state from MQTT — waiting")
                time.sleep(eval_interval)
                continue

            # 4. Evaluate rules
            result = evaluate(system, weather, forecast, config)

            # 5. Apply preset if warranted
            if result.matched_preset is None:
                logger.warning("No preset selected — skipping apply")
                result.preset_applied = False

            elif result.matched_preset == active_preset:
                result.preset_unchanged = True
                logger.debug("Preset unchanged ('%s') — no action", active_preset)

            else:
                # Check cooldown
                now = datetime.now()
                if last_switch_time and (now - last_switch_time) < cooldown_td:
                    remaining = int((cooldown_td - (now - last_switch_time)).total_seconds() / 60)
                    logger.info(
                        "Preset change suppressed by cooldown (%dm remaining) — "
                        "want '%s', have '%s'",
                        remaining, result.matched_preset, active_preset,
                    )
                    result.suppressed = True
                else:
                    preset = presets[result.matched_preset]
                    ok = mqtt.publish_preset(preset, result.reason)
                    if ok:
                        result.preset_applied = True
                        active_preset    = result.matched_preset
                        last_switch_time = datetime.now()
                        logger.info(
                            "Preset switched: '%s' → '%s'",
                            active_preset, result.matched_preset,
                        )

            # 6. Write state file
            eval_history.appendleft({
                "timestamp":      result.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "matched_rule":   result.matched_rule,
                "matched_preset": result.matched_preset,
                "reason":         result.reason,
                "applied":        result.preset_applied,
                "suppressed":     result.suppressed,
                "unchanged":      result.preset_unchanged,
                "inputs": {k: round(v, 1) for k, v in result.inputs.items()},
            })
            write_engine_state(result, active_preset, last_switch_time, eval_history, dry_run)

        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)

        elapsed   = (datetime.now() - loop_start).total_seconds()
        sleep_time = max(0, eval_interval - elapsed)
        logger.debug("Loop took %.1fs, sleeping %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)

    logger.info("Shutting down")
    mqtt.disconnect()


if __name__ == "__main__":
    main()
