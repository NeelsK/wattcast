#!/usr/bin/env python3
"""
WattCast — Main loop
Subscribes to SA MQTT, builds SignalSnapshot every EVAL_INTERVAL_S seconds,
runs the rule engine, and publishes actions for live (non-simulated) rules.

Usage:
    pip install paho-mqtt --break-system-packages
    python3 wattcast.py

Config:
    SA_HOST      — Solar Assistant Pi IP (default 10.69.69.31)
    EVAL_INTERVAL — seconds between rule evaluations (default 30)
    DRY_RUN      — set to 1 to never publish, even for live rules (default 0)

Rules are loaded from rule_engine.py. All rules default to simulate=True.
To make a rule live, edit rule_engine.py and set simulate=False for that rule.
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt

from weather_provider import WeatherProvider
from shelly import ShellySwitch, ShellyRegistry
from rule_engine import (
    Signal, SignalSnapshot, SignalSource,
    SetSetting, SwitchControl,
    RuleEngine, EvalResult,
    build_default_ruleset,
)

# ── Config ────────────────────────────────────────────────────────────────────

SA_HOST        = os.getenv("SA_HOST",        "10.69.69.31")
SA_PORT        = int(os.getenv("SA_PORT",    "1883"))
EVAL_INTERVAL  = int(os.getenv("EVAL_INTERVAL", "30"))   # seconds
DRY_RUN        = os.getenv("DRY_RUN", "0") == "1"        # never publish if set

# Shelly device credentials (set as env vars — never hardcode passwords)
SHELLY_GEYSER_HOST     = os.getenv("SHELLY_GEYSER_HOST",     "10.69.69.43")
SHELLY_GEYSER_PASSWORD = os.getenv("SHELLY_GEYSER_PASSWORD", "")  # required for live control

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wattcast")

# ── MQTT topic → signal key mapping ──────────────────────────────────────────
# Maps SA MQTT topic suffixes to SignalSnapshot keys.
# Add more here as needed — only mapped topics update the snapshot.

TOPIC_MAP: dict[str, tuple[str, SignalSource, str]] = {
    # topic suffix                                signal_key                source                   unit
    # ── Battery (total/ prefix for these two) ──────────────────────────────────
    "total/battery_state_of_charge": ("battery_soc",          SignalSource.INVERTER, "%"),
    "total/battery_power":           ("battery_power",         SignalSource.INVERTER, "W"),
    # ── Everything else is inverter_1/ ────────────────────────────────────────
    "inverter_1/battery_voltage":    ("battery_voltage",       SignalSource.INVERTER, "V"),
    "inverter_1/battery_current":    ("battery_current",       SignalSource.INVERTER, "A"),
    "inverter_1/temperature":        ("battery_temperature",   SignalSource.INVERTER, "°C"),
    "inverter_1/pv_power":           ("pv_power",              SignalSource.INVERTER, "W"),
    "inverter_1/pv_power_1":         ("pv_power_1",            SignalSource.INVERTER, "W"),
    "inverter_1/pv_power_2":         ("pv_power_2",            SignalSource.INVERTER, "W"),
    "inverter_1/pv_voltage_1":       ("pv_voltage_1",          SignalSource.INVERTER, "V"),
    "inverter_1/pv_voltage_2":       ("pv_voltage_2",          SignalSource.INVERTER, "V"),
    "inverter_1/pv_current_1":       ("pv_current_1",          SignalSource.INVERTER, "A"),
    "inverter_1/pv_current_2":       ("pv_current_2",          SignalSource.INVERTER, "A"),
    "inverter_1/load_power":         ("load_power",            SignalSource.INVERTER, "W"),
    "inverter_1/load_power_essential":("load_power_essential", SignalSource.INVERTER, "W"),
    "inverter_1/load_power_non-essential":("load_power_non_essential", SignalSource.INVERTER, "W"),
    "inverter_1/load_percentage":    ("load_percentage",       SignalSource.INVERTER, "%"),
    "inverter_1/grid_power":         ("grid_power",            SignalSource.INVERTER, "W"),
    "inverter_1/grid_power_ld":      ("grid_power_ld",         SignalSource.INVERTER, "W"),
    "inverter_1/grid_power_ct":      ("grid_power_ct",         SignalSource.INVERTER, "W"),
    "inverter_1/grid_voltage":       ("grid_voltage",          SignalSource.INVERTER, "V"),
    "inverter_1/grid_frequency":     ("grid_frequency",        SignalSource.INVERTER, "Hz"),
    "inverter_1/ac_output_voltage":  ("ac_output_voltage",     SignalSource.INVERTER, "V"),
    "inverter_1/ac_output_frequency":("ac_output_frequency",   SignalSource.INVERTER, "Hz"),
    "inverter_1/device_mode":        ("inverter_mode",         SignalSource.INVERTER, ""),   # SA calls it device_mode
    "inverter_1/generator_power":    ("generator_power",       SignalSource.INVERTER, "W"),
}

# ── State ─────────────────────────────────────────────────────────────────────

# Live signal values — updated on every MQTT message
_live: dict[str, Signal] = {}
_mqtt_client: Optional[mqtt.Client] = None
_message_count = 0
_weather_provider: Optional[WeatherProvider] = None
_shelly_registry: ShellyRegistry = ShellyRegistry()


def _update_signal(topic_suffix: str, raw_value: str) -> None:
    """Parse an incoming MQTT message and update the live signal dict."""
    mapping = TOPIC_MAP.get(topic_suffix)
    if not mapping:
        return

    key, source, unit = mapping
    now = datetime.now()

    # String signals stay as strings; everything else parsed as float
    if unit == "" and raw_value not in ("true", "false", "True", "False"):
        try:
            value: object = float(raw_value)
        except ValueError:
            value = raw_value.strip()
    elif raw_value.lower() in ("true", "false"):
        value = raw_value.lower() == "true"
    else:
        try:
            value = float(raw_value)
        except ValueError:
            value = raw_value.strip()

    _live[key] = Signal(key=key, value=value, source=source, unit=unit, timestamp=now)


def _build_snapshot() -> SignalSnapshot:
    """Combine live inverter signals with time signals into a snapshot."""
    snap = SignalSnapshot(signals=dict(_live))  # copy current live state
    now = datetime.now()

    # Always inject fresh time signals
    for key, val in [
        ("hour",        now.hour),
        ("minute",      now.minute),
        ("day_of_week", now.weekday()),   # 0=Mon
        ("month",       now.month),
    ]:
        snap.signals[key] = Signal(key=key, value=val, source=SignalSource.TIME, timestamp=now)

    snap.evaluated_at = now
    return snap


def _apply_action(action: SetSetting | SwitchControl) -> None:
    """Publish an action to the appropriate MQTT topic or Shelly device."""
    if isinstance(action, SetSetting):
        if _mqtt_client is None:
            log.warning("Cannot apply SetSetting — MQTT client not connected")
            return
        topic = f"solar_assistant/{action.topic}/set"
        payload = str(action.value)
        log.info(f"  PUBLISH → {topic} = {payload!r}")
        _mqtt_client.publish(topic, payload)

    elif isinstance(action, SwitchControl):
        if action.protocol == "shelly":
            # Look up the device in the Shelly registry and call it directly via RPC
            switch = _shelly_registry.get(action.device_id)
            if switch is None:
                log.warning(f"  Cannot apply switch action — '{action.device_id}' not in ShellyRegistry")
                return
            ok = switch.set_state(action.state)
            state_str = "ON" if action.state else "OFF"
            if ok:
                log.info(f"  SHELLY RPC → '{action.device_id}' set to {state_str}")
            else:
                log.warning(f"  SHELLY RPC → '{action.device_id}' {state_str} FAILED")

        elif action.protocol == "mqtt" and action.mqtt_topic:
            if _mqtt_client is None:
                log.warning("Cannot apply SwitchControl — MQTT client not connected")
                return
            payload = "on" if action.state else "off"
            log.info(f"  PUBLISH → {action.mqtt_topic} = {payload!r}")
            _mqtt_client.publish(action.mqtt_topic, payload)

        elif action.protocol == "webhook" and action.webhook_url:
            # Webhook calls would go here — for now just log
            log.info(f"  WEBHOOK → {action.webhook_url} state={'on' if action.state else 'off'}")

        else:
            log.warning(f"  Cannot apply switch action — unknown protocol '{action.protocol}' "
                        f"for '{action.device_id}'")


def _run_evaluation(engine: RuleEngine) -> None:
    """Build snapshot, evaluate rules, apply actions."""
    if not _live:
        log.info("Waiting for first MQTT messages before evaluating...")
        return

    snap = _build_snapshot()

    # Inject weather signals into snapshot
    if _weather_provider:
        _weather_provider.inject(snap)

    # One-line status
    soc  = snap.value("battery_soc")
    pv   = snap.value("pv_power")
    load = snap.value("load_power")
    grid = snap.value("grid_power")
    mode = snap.value("inverter_mode")
    irr = snap.value("irradiance")
    log.info(
        f"State: SoC={soc}%  PV={pv}W  Load={load}W  Grid={grid}W  "
        f"Irr={irr}W/m²  Mode={mode!r}  Signals={len(snap.signals)}"
    )

    result: EvalResult = engine.evaluate(snap)
    log.info(f"Eval:  {result.summary()}")

    if result.matched and not result.simulated:
        log.info(f"Applying {len(result.actions)} action(s)...")
        for action in result.actions:
            _apply_action(action)
    elif result.matched and result.simulated:
        log.info("  (simulated — no MQTT published)")


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info(f"Connected to SA MQTT at {SA_HOST}:{SA_PORT}")
        client.subscribe("solar_assistant/#")
        log.info("Subscribed to solar_assistant/#")
    else:
        log.error(f"MQTT connection failed — reason code {reason_code}")


def on_message(client, userdata, msg):
    global _message_count
    topic = msg.topic   # e.g. "solar_assistant/total/battery_state_of_charge/state"
    value = msg.payload.decode("utf-8", errors="replace").strip()

    # Strip prefix "solar_assistant/" and suffix "/state"
    suffix = topic.removeprefix("solar_assistant/")
    if suffix.endswith("/state"):
        suffix = suffix[:-6]

    _update_signal(suffix, value)
    _message_count += 1


def on_disconnect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        log.warning(f"Unexpected disconnect (rc={reason_code}) — will reconnect")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _mqtt_client

    global _weather_provider

    log.info("WattCast starting up")
    log.info(f"  SA host:       {SA_HOST}:{SA_PORT}")
    log.info(f"  Eval interval: {EVAL_INTERVAL}s")
    log.info(f"  Dry run:       {DRY_RUN}")

    # Register Shelly devices
    if SHELLY_GEYSER_PASSWORD:
        _shelly_registry.register(ShellySwitch(
            name="geyser",
            host=SHELLY_GEYSER_HOST,
            password=SHELLY_GEYSER_PASSWORD,
        ))
        log.info(f"  Geyser Shelly: {SHELLY_GEYSER_HOST} (live)")
    else:
        log.warning("  SHELLY_GEYSER_PASSWORD not set — geyser switch control disabled")

    # Start weather provider
    _weather_provider = WeatherProvider()
    _weather_provider.start()

    # Build ruleset and engine
    ruleset = build_default_ruleset()
    engine  = RuleEngine(ruleset, dry_run=DRY_RUN)

    log.info(f"Loaded {len(ruleset.rules)} rules:")
    for rule in ruleset.sorted_rules():
        mode = "SIMULATE" if rule.simulate else "LIVE"
        log.info(f"  [{mode}] P{rule.priority} — {rule.name}")

    # Connect MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wattcast")
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    _mqtt_client = client

    log.info(f"Connecting to {SA_HOST}:{SA_PORT}...")
    client.connect(SA_HOST, SA_PORT, keepalive=60)
    client.loop_start()

    last_eval = 0.0
    try:
        while True:
            now = time.time()
            if now - last_eval >= EVAL_INTERVAL:
                _run_evaluation(engine)
                last_eval = now
            time.sleep(1)

    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        if _weather_provider:
            _weather_provider.stop()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
