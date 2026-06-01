"""
mqtt_client.py — MQTT subscriber + publisher for Solar Assistant integration.

Subscribes to solar_assistant/inverter_N/# and maintains the latest
SystemState. Publishes preset slot settings back to Solar Assistant.

Uses a simple threading model: the MQTT loop runs in its own thread
(standard paho pattern), and the main rule engine loop reads state
via get_system_state().
"""

import json
import logging
import threading
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt

from models import Command, ForecastSummary, Preset, SystemState, WeatherNow

logger = logging.getLogger(__name__)


class SolarAssistantMQTT:
    """
    Thin wrapper around paho-mqtt for Solar Assistant integration.

    Topic layout (Solar Assistant publishes):
        solar_assistant/inverter_1/<measurement>/state  → float payload

    Write topics (we publish):
        solar_assistant/inverter_1/<setting>/set  → string payload

    Preset topics (6 slots each):
        solar_assistant/inverter_1/time_point_N/set      → "HH:MM"
        solar_assistant/inverter_1/capacity_point_N/set  → "40"
        solar_assistant/inverter_1/charge_point_N/set    → "True" / "False"
    """

    def __init__(self, config: dict):
        mqtt_cfg = config["mqtt"]
        self._host = mqtt_cfg["host"]
        self._port = mqtt_cfg.get("port", 1883)
        self._inverter_id = mqtt_cfg.get("inverter_id", "inverter_1")
        self._username = mqtt_cfg.get("username", "")
        self._password = mqtt_cfg.get("password", "")
        self._dry_run: bool = config["engine"].get("dry_run", True)

        # Latest readings — updated by MQTT callbacks
        self._lock = threading.Lock()
        self._readings: dict[str, float] = {}
        self._last_updated: Optional[datetime] = None

        self._client = mqtt.Client(
            client_id="solar-rule-engine",
            clean_session=True,
        )
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    # -----------------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------------

    def connect(self) -> None:
        logger.info("Connecting to MQTT broker at %s:%d", self._host, self._port)
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # -----------------------------------------------------------------------
    # Callbacks
    # -----------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = f"solar_assistant/{self._inverter_id}/#"
            client.subscribe(topic)
            client.subscribe("solar_assistant/total/#")
            logger.info("MQTT connected, subscribed to solar_assistant/#")
            self._publish_ha_discovery()
        else:
            logger.error("MQTT connection failed, rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT unexpectedly disconnected (rc=%d), will auto-reconnect", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = float(msg.payload.decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            return

        parts = msg.topic.split("/")
        if len(parts) >= 3 and parts[-1] == "state":
            measurement = parts[-2]
            with self._lock:
                self._readings[measurement] = payload
                self._last_updated = datetime.now()

    # -----------------------------------------------------------------------
    # Home Assistant MQTT Discovery
    # -----------------------------------------------------------------------

    _HA_SENSORS = [
        ("irradiance",                           "Solar Irradiance",               "W/m²",  "irradiance",              "measurement"),
        ("temperature",                          "Outdoor Temperature",            "°C",    "temperature",             "measurement"),
        ("humidity",                             "Outdoor Humidity",               "%",     "humidity",                "measurement"),
        ("wind_speed",                           "Wind Speed",                     "m/s",   "wind_speed",              "measurement"),
        ("rain_rate",                            "Rain Rate",                      "mm/h",  "precipitation_intensity", "measurement"),
        ("pv_forecast_today_remaining",          "PV Forecast Today Remaining",    "kWh",   "energy",                  "measurement"),
        ("pv_forecast_tomorrow",                 "PV Forecast Tomorrow",           "kWh",   "energy",                  "measurement"),
        ("pv_forecast_peak_gti",                 "PV Forecast Peak GTI Today",     "W/m²",  "irradiance",              "measurement"),
        ("pv_forecast_tomorrow_rain_probability","Tomorrow Rain Probability",      "%",     None,                      "measurement"),
        ("active_preset",                        "Active Inverter Preset",         None,    None,                      "measurement"),
    ]

    _HA_NODE_ID = "wattcast_weather"

    def _publish_ha_discovery(self) -> None:
        device = {
            "identifiers": [self._HA_NODE_ID],
            "name": "WattCast Weather",
            "model": "Ecowitt HP2553CA",
            "manufacturer": "WattCast",
        }
        for object_id, name, unit, device_class, state_class in self._HA_SENSORS:
            state_topic = f"wattcast/weather/{object_id}/state"
            config: dict = {
                "name": name,
                "unique_id": f"{self._HA_NODE_ID}_{object_id}",
                "state_topic": state_topic,
                "state_class": state_class,
                "device": device,
            }
            if unit:
                config["unit_of_measurement"] = unit
            if device_class:
                config["device_class"] = device_class
            discovery_topic = f"homeassistant/sensor/{self._HA_NODE_ID}/{object_id}/config"
            self._client.publish(discovery_topic, payload=json.dumps(config), qos=1, retain=True)
        logger.info("Home Assistant MQTT discovery published (%d sensors)", len(self._HA_SENSORS))

    # -----------------------------------------------------------------------
    # State access
    # -----------------------------------------------------------------------

    def get_system_state(self) -> Optional[SystemState]:
        with self._lock:
            r = dict(self._readings)
            updated = self._last_updated

        required = {"battery_state_of_charge", "pv_power", "load_power"}
        missing = required - r.keys()
        if missing:
            logger.warning("Missing MQTT readings: %s — cannot build SystemState", missing)
            return None

        return SystemState(
            battery_soc=r.get("battery_state_of_charge", 0.0),
            pv_power=r.get("pv_power", 0.0),
            load_power=r.get("load_power", 0.0),
            grid_power=r.get("grid_power", 0.0),
            battery_power=r.get("battery_power", 0.0),
            battery_voltage=r.get("battery_voltage", 0.0),
            battery_current=r.get("battery_current", 0.0),
            timestamp=updated or datetime.now(),
        )

    def get_raw_readings(self) -> dict[str, float]:
        with self._lock:
            return dict(self._readings)

    # -----------------------------------------------------------------------
    # Publishing
    # -----------------------------------------------------------------------

    def publish_weather(self, weather: WeatherNow, forecast: "ForecastSummary | None" = None) -> None:
        readings: dict[str, object] = {
            "irradiance": weather.irradiance,
            "temperature": weather.temperature,
            "humidity": weather.humidity,
            "wind_speed": weather.wind_speed,
            "rain_rate": weather.rain_rate,
        }
        if forecast is not None:
            readings["pv_forecast_today_remaining"] = forecast.today_remaining_yield_kwh
            readings["pv_forecast_tomorrow"] = forecast.tomorrow_yield_kwh
            readings["pv_forecast_peak_gti"] = forecast.today_peak_gti
            readings["pv_forecast_tomorrow_rain_probability"] = forecast.tomorrow_rain_probability
        for field, value in readings.items():
            topic = f"wattcast/weather/{field}/state"
            self._client.publish(topic, payload=f"{value:.2f}", qos=0, retain=True)
        logger.debug(
            "Weather published: irradiance=%.0f W/m²  temp=%.1f°C  rain=%.1f mm/hr",
            weather.irradiance, weather.temperature, weather.rain_rate,
        )

    def publish_preset(self, preset: Preset, reason: str) -> bool:
        """
        Apply a preset by publishing all 18 slot topics to Solar Assistant.

        Writes atomically in order: time_point → capacity_point → charge_point
        for each slot.

        If dry_run is True, logs all topics but does NOT publish.

        Returns True if published (or dry-run), False on MQTT error.
        """
        inverter = self._inverter_id
        topics: list[tuple[str, str]] = []

        for i, slot in enumerate(preset.slots, start=1):
            topics.append((f"solar_assistant/{inverter}/time_point_{i}/set",     slot.time))
            topics.append((f"solar_assistant/{inverter}/capacity_point_{i}/set", str(slot.capacity)))
            topics.append((f"solar_assistant/{inverter}/charge_point_{i}/set",   str(slot.grid_charge)))

        if self._dry_run:
            logger.info("[DRY RUN] Would apply preset '%s': %s", preset.name, reason)
            for topic, value in topics:
                logger.info("[DRY RUN]   %s = %r", topic, value)
            # Still publish active_preset to MQTT for dashboard visibility
            self._client.publish(
                f"wattcast/weather/active_preset/state",
                payload=f"[DRY RUN] {preset.name}",
                qos=0, retain=True,
            )
            return True

        errors = 0
        for topic, value in topics:
            result = self._client.publish(topic, payload=value, qos=1, retain=False)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug("Published: %s = %r", topic, value)
            else:
                logger.error("Publish failed: %s rc=%d", topic, result.rc)
                errors += 1

        if errors == 0:
            logger.info("Preset '%s' applied (%d topics) — %s", preset.name, len(topics), reason)
            self._client.publish(
                "wattcast/weather/active_preset/state",
                payload=preset.name,
                qos=0, retain=True,
            )
            return True
        else:
            logger.error("Preset '%s' had %d publish errors", preset.name, errors)
            return False

    def publish_command(self, cmd: Command) -> None:
        """Publish a single Command (legacy — kept for compatibility)."""
        full_topic = f"solar_assistant/{self._inverter_id}/{cmd.topic_suffix}/set"
        if self._dry_run:
            logger.info("[DRY RUN] Would publish: %s = %r  (%s)", full_topic, cmd.value, cmd.reason)
            return
        result = self._client.publish(full_topic, payload=cmd.value, qos=1, retain=False)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info("Published: %s = %r  (%s)", full_topic, cmd.value, cmd.reason)
        else:
            logger.error("Publish failed for %s: rc=%d", full_topic, result.rc)
