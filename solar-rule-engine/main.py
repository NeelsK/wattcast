"""
main.py — Entry point. Wires data sources, rule engine, and MQTT together.

Run with:
    python main.py --config config.yaml

Systemd service or cron can manage restarts.
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
from engine import evaluate
from hysteresis import HysteresisTracker
from models import ForecastSummary
from mqtt_client import SolarAssistantMQTT
from switches.manager import SwitchManager
from switches.state_tracker import SwitchStateRegistry


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


STATE_FILE = os.getenv("ENGINE_STATE_FILE", "/tmp/wattcast_engine_state.json")
HISTORY_MAX = 20


def write_engine_state(state: dict, history: deque) -> None:
    """Write current engine state + rolling history to a JSON file for the status API."""
    try:
        payload = {**state, "history": list(history)}
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logging.getLogger("main").warning("Failed to write engine state: %s", e)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Solar Rule Engine")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    engine_cfg = config["engine"]
    setup_logging(engine_cfg.get("log_level", "INFO"))

    logger = logging.getLogger("main")

    if engine_cfg.get("dry_run", True):
        logger.warning("DRY RUN MODE — commands will be logged but NOT sent to inverter")

    # --- Setup ---
    mqtt = SolarAssistantMQTT(config)
    hysteresis = HysteresisTracker(
        min_interval_minutes=engine_cfg.get("hysteresis_minutes", 15)
    )
    switch_manager = SwitchManager(config)
    switch_registry = SwitchStateRegistry(config)
    forecast: Optional[ForecastSummary] = None
    forecast_fetched_at: Optional[datetime] = None
    forecast_refresh_td = timedelta(minutes=engine_cfg.get("forecast_refresh_minutes", 60))
    eval_interval = engine_cfg.get("eval_interval_seconds", 300)

    eval_history: deque = deque(maxlen=HISTORY_MAX)

    # Graceful shutdown
    running = True
    def _shutdown(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # --- Connect MQTT and wait for initial readings ---
    mqtt.connect()
    logger.info("Waiting 10s for initial MQTT readings from Solar Assistant...")
    time.sleep(10)

    # --- Main loop ---
    logger.info("Starting rule engine loop (interval: %ds)", eval_interval)

    while running:
        loop_start = datetime.now()

        try:
            # Refresh forecast if stale or not yet fetched
            if forecast is None or (datetime.now() - forecast_fetched_at) > forecast_refresh_td:
                logger.info("Refreshing Open-Meteo forecast...")
                try:
                    forecast = openmeteo.fetch(config)
                    forecast_fetched_at = datetime.now()
                except Exception as e:
                    logger.error("Forecast fetch failed: %s", e)
                    if forecast is None:
                        logger.warning("No forecast available — skipping this cycle")
                        time.sleep(eval_interval)
                        continue

            # Fetch live weather station reading
            weather_source = config.get("weather_source", "ecowitt")
            try:
                if weather_source == "mariadb":
                    import sources.mariadb as mariadb_src
                    weather = mariadb_src.fetch(config)
                elif weather_source == "openmeteo":
                    # Use forecast irradiance as a proxy for live irradiance
                    from models import WeatherNow
                    weather = WeatherNow(
                        irradiance=0.0,
                        temperature=20.0,
                        humidity=50.0,
                        wind_speed=0.0,
                        rain_rate=0.0,
                    )
                else:
                    import sources.ecowitt as ecowitt
                    weather = ecowitt.fetch(config)
                logger.debug(
                    "Weather (%s): irradiance=%.0f W/m²  temp=%.1f°C  rain=%.1f mm/hr",
                    weather_source, weather.irradiance, weather.temperature, weather.rain_rate,
                )
            except Exception as e:
                logger.error("Weather fetch failed (%s): %s", weather_source, e)
                time.sleep(eval_interval)
                continue

            # Publish weather data to MQTT
            mqtt.publish_weather(weather, forecast)

            # Get system state from MQTT
            system = mqtt.get_system_state()
            if system is None:
                logger.warning("No system state yet — waiting for MQTT readings")
                time.sleep(eval_interval)
                continue

            logger.debug(
                "System: SOC=%.0f%%  PV=%.0fW  Load=%.0fW  Grid=%.0fW",
                system.battery_soc, system.pv_power, system.load_power, system.grid_power,
            )

            # Run the decision engine
            commands, switch_actions = evaluate(system, weather, forecast, config)

            # Apply hysteresis filter to inverter commands
            approved = hysteresis.filter(commands)
            skipped = len(commands) - len(approved)
            if skipped:
                logger.debug("%d inverter command(s) suppressed by hysteresis", skipped)

            # Publish approved inverter commands
            for cmd in approved:
                mqtt.publish_command(cmd)

            # Execute switch actions — guarded by SwitchRunState
            for action in switch_actions:
                name = action.switch_name
                if name not in switch_manager:
                    logger.warning("Engine produced action for unknown switch '%s'", name)
                    continue

                state = switch_registry[name]
                sw = switch_manager[name]

                if action.turn_on:
                    allowed, block_reason = state.can_turn_on()
                    if allowed:
                        ok = sw.turn_on(reason=action.reason)
                        if ok:
                            state.record_turn_on()
                    else:
                        logger.debug(
                            "Switch '%s' turn-on blocked by guard: %s", name, block_reason
                        )
                else:
                    allowed, block_reason = state.can_turn_off()
                    if allowed:
                        ok = sw.turn_off(reason=action.reason)
                        if ok:
                            state.record_turn_off()
                    else:
                        logger.debug(
                            "Switch '%s' turn-off blocked by guard: %s", name, block_reason
                        )

            # Log switch states periodically
            switch_registry.log_all()

            # Persist engine state for status dashboard
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            eval_entry = {
                "timestamp": now_str,
                "inputs": {
                    "soc": round(system.battery_soc, 1),
                    "pv_power": round(system.pv_power, 0),
                    "load_power": round(system.load_power, 0),
                    "grid_power": round(system.grid_power, 0),
                    "battery_power": round(system.battery_power, 0),
                    "net_surplus": round(system.net_solar_surplus, 0),
                    "raining": weather.is_raining,
                    "irradiance": round(weather.irradiance, 0),
                    "temp": round(weather.temperature, 1),
                },
                "forecast": {
                    "today_remaining_kwh": round(forecast.today_remaining_yield_kwh, 2),
                    "tomorrow_kwh": round(forecast.tomorrow_yield_kwh, 2),
                    "tomorrow_rain_pct": round(forecast.tomorrow_rain_probability * 100, 0),
                    "tomorrow_rain_hours": forecast.tomorrow_rain_hours,
                    "age_minutes": round(forecast.age_minutes, 1),
                },
                "commands": [
                    {"topic": c.topic_suffix, "value": c.value, "reason": c.reason}
                    for c in commands
                ],
                "switch_actions": [
                    {"switch": a.switch_name, "turn_on": a.turn_on, "reason": a.reason}
                    for a in switch_actions
                ],
                "approved_commands": [
                    {"topic": c.topic_suffix, "value": c.value, "reason": c.reason}
                    for c in approved
                ],
                "dry_run": engine_cfg.get("dry_run", True),
            }
            eval_history.appendleft(eval_entry)
            write_engine_state({"last_eval": eval_entry, "dry_run": engine_cfg.get("dry_run", True)}, eval_history)

        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)

        # Sleep for the remainder of the interval
        elapsed = (datetime.now() - loop_start).total_seconds()
        sleep_time = max(0, eval_interval - elapsed)
        logger.debug("Loop took %.1fs, sleeping %.1fs", elapsed, sleep_time)
        time.sleep(sleep_time)

    # --- Cleanup ---
    logger.info("Shutting down")
    mqtt.disconnect()


if __name__ == "__main__":
    main()
