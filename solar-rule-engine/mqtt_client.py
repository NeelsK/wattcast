"""
mqtt_client.py — MQTT subscriber + publisher for Solar Assistant integration.

Subscribes to solar_assistant/inverter_N/# and maintains the latest
SystemState. Publishes Command objects back to Solar Assistant set topics.

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

from models import Command, ForecastSummary, SystemState, WeatherNow

logger = logging.getLogger(__name__)


class SolarAssistantMQTT:
    """
    Thin wrapper around paho-mqtt for Solar Assistant integration.

    Topic layout (Solar Assistant publishes):
        solar_assistant/inverter_1/<measurement>/state  → float payload

    Write topics (we publish):
        solar_assistant/inverter_1/<setting>/set  → string payload
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
        self._client.loop_start()  # Background thread

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
            # Also subscribe to totals (battery SOC is under solar_assistant/total/)
            client.subscribe("solar_assistant/total/#")
            logger.info("MQTT connected, subscribed to solar_assistant/#")
            self._publish_ha_discovery()
        else:
            logger.error("MQTT connection failed, rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT unexpectedly disconnected (rc=%d), will auto-reconnect", rc)

    def _on_message(self, client, userdata, msg):
        """
        Parse incoming Solar Assistant MQTT messages.
        Topics end in /state and carry a plain float payload.
        """
        try:
            payload = float(msg.payload.decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            return  # Ignore non-numeric payloads (JSON discovery messages etc.)

        # Extract the measurement name from topic
        # e.g. solar_assistant/inverter_1/battery_state_of_charge/state → battery_state_of_charge
        parts = msg.topic.split("/")
        if len(parts) >= 3 and parts[-1] == "state":
            measurement = parts[-2]
            with self._lock:
                self._readings[measurement] = payload
                self._last_updated = datetime.now()

    # -----------------------------------------------------------------------
    # Home Assistant MQTT Discovery
    # -----------------------------------------------------------------------

    # Sensor definitions: (object_id, friendly_name, unit, device_class, state_class)
    # device_class: https://developers.home-assistant.io/docs/core/entity/sensor/#available-device-classes
    # state_class: "measurement" for live values, "total_increasing" for counters
    _HA_SENSORS = [
        ("irradiance",                       "Solar Irradiance",               "W/m²",  "irradiance",        "measurement"),
        ("temperature",                      "Outdoor Temperature",            "°C",    "temperature",       "measurement"),
        ("humidity",                         "Outdoor Humidity",               "%",     "humidity",          "measurement"),
        ("wind_speed",                       "Wind Speed",                     "m/s",   "wind_speed",        "measurement"),
        ("rain_rate",                        "Rain Rate",                      "mm/h",  "precipitation_intensity", "measurement"),
        ("pv_forecast_today_remaining",      "PV Forecast Today Remaining",    "kWh",   "energy",            "measurement"),
        ("pv_forecast_tomorrow",             "PV Forecast Tomorrow",           "kWh",   "energy",            "measurement"),
        ("pv_forecast_peak_gti",             "PV Forecast Peak GTI Today",     "W/m²",  "irradiance",        "measurement"),
        ("pv_forecast_tomorrow_rain_probability", "Tomorrow Rain Probability", "%",     None,                "measurement"),
    ]

    _HA_NODE_ID = "wattcast_weather"

    def _publish_ha_discovery(self) -> None:
        """
        Publish Home Assistant MQTT discovery config for all weather sensors.
        Called once on connect. HA auto-creates entities from these retained messages.
        Discovery topic: homeassistant/sensor/<node_id>/<object_id>/config
        """
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
                "unit_of_measurement": unit,
                "state_class": state_class,
                "device": device,
            }
            if device_class:
                config["device_class"] = device_class

            discovery_topic = f"homeassistant/sensor/{self._HA_NODE_ID}/{object_id}/config"
            payload = json.dumps(config)
            self._client.publish(discovery_topic, payload=payload, qos=1, retain=True)
            logger.debug("HA discovery published: %s", discovery_topic)

        logger.info("Home Assistant MQTT discovery published (%d sensors)", len(self._HA_SENSORS))

    # -----------------------------------------------------------------------
    # State access
    # -----------------------------------------------------------------------

    def get_system_state(self) -> Optional[SystemState]:
        """
        Build a SystemState from latest MQTT readings.
        Returns None if required readings are missing.
        """
        with self._lock:
            r = dict(self._readings)
            updated = self._last_updated

        required = {
            "battery_state_of_charge",
            "pv_power",
            "load_power",
        }
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
        """Return a copy of all raw readings — useful for debugging."""
        with self._lock:
            return dict(self._readings)

    # -----------------------------------------------------------------------
    # Publishing
    # -----------------------------------------------------------------------

    def publish_weather(self, weather: WeatherNow, forecast: "ForecastSummary | None" = None) -> None:
        """
        Publish weather station readings (and optionally forecast) to MQTT.

        Topics follow the Solar Assistant /state convention:
            wattcast/weather/<field>/state  → float payload

        Always publishes even in dry_run mode — weather is read-only data,
        not an inverter command.
        """
        readings: dict[str, float] = {
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
            payload = f"{value:.2f}"
            result = self._client.publish(topic, payload=payload, qos=0, retain=True)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error("Failed to publish weather %s: rc=%d", topic, result.rc)

        logger.debug(
            "Weather published to MQTT: irradiance=%.0f W/m²  temp=%.1f°C  rain=%.1f mm/hr",
            weather.irradiance, weather.temperature, weather.rain_rate,
        )

    def publish_command(self, cmd: Command) -> None:
        """
        Publish a Command to Solar Assistant.

        If dry_run is True, logs the command but does NOT publish.
        Full topic: solar_assistant/<inverter_id>/<topic_suffix>/set
        """
        full_topic = f"solar_assistant/{self._inverter_id}/{cmd.topic_suffix}/set"

        if self._dry_run:
            logger.info("[DRY RUN] Would publish: %s = %r  (%s)", full_topic, cmd.value, cmd.reason)
            return

        result = self._client.publish(full_topic, payload=cmd.value, qos=1, retain=False)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info("Published: %s = %r  (%s)", full_topic, cmd.value, cmd.reason)
        else:
            logger.error("Publish failed for %s: rc=%d", full_topic, result.rc)
