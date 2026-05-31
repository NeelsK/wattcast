#!/usr/bin/env python3
"""
WattCast PoC — MQTT subscriber + state logger
Connects to Solar Assistant MQTT broker and maintains live inverter state.

Usage:
    pip install paho-mqtt
    python3 wattcast_poc.py

Config:
    Set SA_HOST below, or export SA_HOST=10.69.69.31
"""

import os
import time
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
import paho.mqtt.client as mqtt

# ── Config ────────────────────────────────────────────────────────────────────

SA_HOST = os.getenv("SA_HOST", "10.69.69.31")
SA_PORT = int(os.getenv("SA_PORT", "1883"))
LOG_INTERVAL_S = 10  # print state summary every N seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wattcast")

# ── Inverter state ─────────────────────────────────────────────────────────────

@dataclass
class InverterState:
    """Live state of the inverter, updated from MQTT messages."""

    # Battery
    battery_soc: Optional[float] = None          # %
    battery_voltage: Optional[float] = None      # V
    battery_current: Optional[float] = None      # A  (negative = discharging)
    battery_power: Optional[float] = None        # W  (negative = discharging)
    battery_temperature: Optional[float] = None  # °C

    # Solar
    pv_power: Optional[float] = None             # W total
    pv_power_1: Optional[float] = None           # W string 1
    pv_power_2: Optional[float] = None           # W string 2
    pv_voltage_1: Optional[float] = None         # V string 1
    pv_voltage_2: Optional[float] = None         # V string 2

    # Grid
    grid_power: Optional[float] = None           # W (positive = importing)
    grid_voltage: Optional[float] = None         # V
    grid_frequency: Optional[float] = None       # Hz

    # Load
    load_power: Optional[float] = None           # W total
    load_power_essential: Optional[float] = None # W essential circuits
    load_power_non_essential: Optional[float] = None  # W non-essential
    load_percentage: Optional[float] = None      # %

    # Inverter
    device_mode: Optional[str] = None            # e.g. "Charge below 60%"
    temperature: Optional[float] = None          # °C inverter temp
    ac_output_voltage: Optional[float] = None    # V
    ac_output_frequency: Optional[float] = None  # Hz

    # Metadata
    last_updated: Optional[datetime] = None
    message_count: int = field(default=0, repr=False)

    def update(self, topic: str, value: str) -> None:
        """Parse an MQTT message and update the relevant field."""
        # Strip prefix: solar_assistant/inverter_1/ or solar_assistant/total/
        parts = topic.split("/")
        if len(parts) < 3:
            return
        measurement = "/".join(parts[2:])  # strip device prefix, keep measurement/state

        # Remove trailing /state
        if measurement.endswith("/state"):
            measurement = measurement[:-6]

        # Map topic measurement to field
        mapping = {
            "battery_state_of_charge": "battery_soc",
            "battery_voltage":         "battery_voltage",
            "battery_current":         "battery_current",
            "battery_power":           "battery_power",
            "battery_temperature":     "battery_temperature",
            "pv_power":                "pv_power",
            "pv_power_1":              "pv_power_1",
            "pv_power_2":              "pv_power_2",
            "pv_voltage_1":            "pv_voltage_1",
            "pv_voltage_2":            "pv_voltage_2",
            "grid_power":              "grid_power",
            "grid_voltage":            "grid_voltage",
            "grid_frequency":          "grid_frequency",
            "load_power":              "load_power",
            "load_power_essential":    "load_power_essential",
            "load_power_non-essential":"load_power_non_essential",
            "load_percentage":         "load_percentage",
            "device_mode":             "device_mode",
            "temperature":             "temperature",
            "ac_output_voltage":       "ac_output_voltage",
            "ac_output_frequency":     "ac_output_frequency",
        }

        attr = mapping.get(measurement)
        if attr is None:
            return

        # Parse value — device_mode is a string, everything else is float
        if attr == "device_mode":
            setattr(self, attr, value.strip())
        else:
            try:
                setattr(self, attr, float(value))
            except ValueError:
                log.warning(f"Could not parse value for {measurement}: {value!r}")
                return

        self.last_updated = datetime.now()
        self.message_count += 1

    def summary(self) -> str:
        """One-line status summary."""
        def fmt(v, unit="", decimals=1):
            return f"{v:.{decimals}f}{unit}" if v is not None else "?"

        charging = (self.battery_current or 0) > 0
        direction = "↑charging" if charging else "↓discharging"

        return (
            f"SoC={fmt(self.battery_soc,'%',0)}  "
            f"Batt={fmt(self.battery_voltage,'V')} {fmt(self.battery_power,'W',0)} {direction}  "
            f"PV={fmt(self.pv_power,'W',0)}  "
            f"Load={fmt(self.load_power,'W',0)}  "
            f"Grid={fmt(self.grid_power,'W',0)}  "
            f"Mode={self.device_mode or '?'}"
        )


# ── Decision engine stub ───────────────────────────────────────────────────────

def evaluate(state: InverterState) -> Optional[str]:
    """
    WattCast decision engine — stub for now.

    Returns an MQTT topic suffix and value to publish, or None if no action needed.
    Example return: ("inverter_1/work_mode/set", "Battery first")

    TODO: incorporate loadshedding schedule + weather forecast here.
    """
    if state.battery_soc is None:
        return None

    # Example rule: if battery > 95% and grid available, switch to utility first
    # (placeholder — real logic will use loadshedding schedule + PV forecast)
    # if state.battery_soc > 95 and (state.grid_voltage or 0) > 200:
    #     return ("solar_assistant/inverter_1/output_source_priority/set", "Utility first")

    return None  # no action


# ── MQTT client ───────────────────────────────────────────────────────────────

state = InverterState()

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info(f"Connected to Solar Assistant MQTT at {SA_HOST}:{SA_PORT}")
        client.subscribe("solar_assistant/#")
        log.info("Subscribed to solar_assistant/#")
    else:
        log.error(f"Connection failed with code {reason_code}")

def on_message(client, userdata, msg):
    topic = msg.topic
    value = msg.payload.decode("utf-8", errors="replace").strip()
    state.update(topic, value)

def on_disconnect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        log.warning(f"Unexpected disconnect (rc={reason_code}) — will auto-reconnect")


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wattcast-poc")
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    # Auto-reconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    log.info(f"Connecting to {SA_HOST}:{SA_PORT}...")
    client.connect(SA_HOST, SA_PORT, keepalive=60)
    client.loop_start()

    last_log = 0.0
    try:
        while True:
            now = time.time()
            if now - last_log >= LOG_INTERVAL_S:
                if state.last_updated:
                    log.info(state.summary())

                    # Run decision engine
                    action = evaluate(state)
                    if action:
                        topic, value = action
                        log.info(f"ACTION → publish {topic} = {value!r}")
                        client.publish(topic, value)
                else:
                    log.info("Waiting for first MQTT message...")
                last_log = now
            time.sleep(0.5)

    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
