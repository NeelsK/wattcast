"""
mqtt_client.py — MQTT subscriber + publisher for Solar Assistant integration.

Subscribes to solar_assistant/inverter_N/# and maintains the latest
SystemState. Publishes Command objects back to Solar Assistant set topics.

Uses a simple threading model: the MQTT loop runs in its own thread
(standard paho pattern), and the main rule engine loop reads state
via get_system_state().
"""

import logging
import threading
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt

from models import Command, SystemState

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
