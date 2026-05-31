#!/usr/bin/env python3
"""
WattCast Rule Engine — Data Model & Evaluator
Based on: /WattCast/design/core-abstractions.md
SA settings reference: /WattCast/research/solar-assistant-api-reference.md

Abstractions implemented:
  Signal      — a named live value (battery_soc, pv_power, loadshedding_active, ...)
  Condition   — a comparison against a Signal (battery_soc < 30)
  Action      — what to do when a rule fires (SetSetting, SwitchControl)
  Rule        — priority-ordered: name + conditions (AND) + action + enabled
  RuleSet     — ordered list of Rules, first-match-wins for inverter rules
  RuleEngine  — evaluates RuleSet against a SignalSnapshot, returns Actions to apply
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

log = logging.getLogger("wattcast.rules")


# ── Signals ───────────────────────────────────────────────────────────────────

class SignalSource(Enum):
    INVERTER   = "inverter"    # from SA MQTT / REST
    WEATHER    = "weather"     # from weather station or forecast API
    FORECAST   = "forecast"    # PV / irradiance forecast
    TIME       = "time"        # derived from system clock
    LOADSHED   = "loadshed"    # from loadshedding schedule provider


@dataclass
class Signal:
    """
    A named value from any source that rules can reference.
    Missing signals (None value) cause any condition referencing them to be skipped.
    """
    key: str               # e.g. "battery_soc", "pv_power", "loadshedding_active"
    value: Any             # float, bool, str, int
    source: SignalSource
    unit: str = ""
    timestamp: Optional[datetime] = None

    def is_available(self) -> bool:
        return self.value is not None


@dataclass
class SignalSnapshot:
    """
    All signals available at a given evaluation time.
    Rules read from here — never fetch live during evaluation.
    """
    signals: dict[str, Signal] = field(default_factory=dict)
    evaluated_at: datetime = field(default_factory=datetime.now)

    def get(self, key: str) -> Optional[Signal]:
        return self.signals.get(key)

    def value(self, key: str) -> Optional[Any]:
        s = self.signals.get(key)
        return s.value if s else None

    @classmethod
    def from_inverter_state(cls, state: dict[str, Any]) -> "SignalSnapshot":
        """Build a snapshot from an SA MQTT/REST metrics dict."""
        snap = cls()
        mapping = {
            # Inverter / battery
            "battery_soc":          ("total/battery_state_of_charge", SignalSource.INVERTER, "%"),
            "battery_voltage":      ("total/battery_voltage",          SignalSource.INVERTER, "V"),
            "battery_power":        ("total/battery_power",            SignalSource.INVERTER, "W"),
            "battery_current":      ("total/battery_current",          SignalSource.INVERTER, "A"),
            "pv_power":             ("total/pv_power",                 SignalSource.INVERTER, "W"),
            "load_power":           ("total/load_power",               SignalSource.INVERTER, "W"),
            "grid_power":           ("total/grid_power",               SignalSource.INVERTER, "W"),
            "grid_voltage":         ("total/grid_voltage",             SignalSource.INVERTER, "V"),
            "grid_frequency":       ("total/grid_frequency",           SignalSource.INVERTER, "Hz"),
            "inverter_mode":        ("total/inverter_mode",            SignalSource.INVERTER, ""),
            "ac_output_voltage":    ("total/ac_output_voltage",        SignalSource.INVERTER, "V"),
            # Time (populated separately)
            "hour":                 (None, SignalSource.TIME, ""),
            "minute":               (None, SignalSource.TIME, ""),
            "day_of_week":          (None, SignalSource.TIME, ""),   # 0=Mon
            "month":                (None, SignalSource.TIME, ""),
            # Loadshedding (populated by LoadsheddingProvider)
            "loadshedding_active":          (None, SignalSource.LOADSHED, ""),
            "loadshedding_starts_in_hours": (None, SignalSource.LOADSHED, "h"),
            "loadshedding_ends_in_hours":   (None, SignalSource.LOADSHED, "h"),
            "loadshedding_stage":           (None, SignalSource.LOADSHED, ""),
            "loadshedding_duration_hours":  (None, SignalSource.LOADSHED, "h"),
            # Weather / forecast (populated by WeatherProvider)
            "irradiance":           (None, SignalSource.WEATHER,  "W/m²"),
            "irradiance_30m_avg":   (None, SignalSource.WEATHER,  "W/m²"),
            "weather_condition":    (None, SignalSource.WEATHER,  ""),
            "pv_forecast_today":    (None, SignalSource.FORECAST, "kWh"),
            "pv_forecast_remaining":(None, SignalSource.FORECAST, "kWh"),
            "pv_forecast_tomorrow": (None, SignalSource.FORECAST, "kWh"),
        }

        now = datetime.now()

        # Populate from inverter state dict (keyed by SA topic)
        topic_to_key = {v[0]: k for k, v in mapping.items() if v[0]}
        for topic, value in state.items():
            key = topic_to_key.get(topic)
            if key:
                _, source, unit = mapping[key]
                snap.signals[key] = Signal(key=key, value=value, source=source,
                                           unit=unit, timestamp=now)

        # Populate time signals
        snap.signals["hour"]        = Signal("hour",        now.hour,        SignalSource.TIME, timestamp=now)
        snap.signals["minute"]      = Signal("minute",      now.minute,      SignalSource.TIME, timestamp=now)
        snap.signals["day_of_week"] = Signal("day_of_week", now.weekday(),   SignalSource.TIME, timestamp=now)
        snap.signals["month"]       = Signal("month",       now.month,       SignalSource.TIME, timestamp=now)

        return snap


# ── Conditions ────────────────────────────────────────────────────────────────

class Operator(Enum):
    LT  = "<"
    LTE = "<="
    GT  = ">"
    GTE = ">="
    EQ  = "=="
    NEQ = "!="
    IN  = "in"      # value in [list]


@dataclass
class Condition:
    """
    A single condition referencing a Signal.
    E.g.:  battery_soc < 30
           loadshedding_active == True
           hour >= 8
           month in [6, 7, 8]

    If the signal is unavailable (None), evaluate() returns None (unknown/skip).
    """
    signal_key: str
    operator: Operator
    threshold: Any          # scalar or list for IN operator

    def evaluate(self, snapshot: SignalSnapshot) -> Optional[bool]:
        """Returns True/False if signal available, None if signal missing."""
        signal = snapshot.get(self.signal_key)
        if signal is None or not signal.is_available():
            log.debug(f"  Condition skip — signal '{self.signal_key}' unavailable")
            return None

        v = signal.value
        t = self.threshold
        op = self.operator

        if op == Operator.LT:  return v < t
        if op == Operator.LTE: return v <= t
        if op == Operator.GT:  return v > t
        if op == Operator.GTE: return v >= t
        if op == Operator.EQ:  return v == t
        if op == Operator.NEQ: return v != t
        if op == Operator.IN:  return v in t
        return None

    def __str__(self) -> str:
        return f"{self.signal_key} {self.operator.value} {self.threshold}"


def C(signal_key: str, op: str, threshold: Any) -> Condition:
    """Shorthand: C('battery_soc', '<', 30)"""
    return Condition(signal_key=signal_key,
                     operator=Operator(op),
                     threshold=threshold)


# ── Actions ───────────────────────────────────────────────────────────────────

@dataclass
class SetSetting:
    """
    Write a value to an inverter setting via SA MQTT.
    topic: SA MQTT topic suffix (e.g. 'inverter_1/capacity_point_1')
    value: the value to set (str, int, float, bool)
    """
    topic: str
    value: Any
    label: str = ""         # human-readable description for logs

    def mqtt_topic(self) -> str:
        """Full MQTT topic for SA write."""
        return f"solar_assistant/{self.topic}/set"

    def __str__(self) -> str:
        desc = self.label or self.topic
        return f"Set {desc} = {self.value}"


@dataclass
class SwitchControl:
    """
    Turn a WiFi smart switch ON or OFF.
    device_id: name matching the ShellyRegistry key (or MQTT/webhook target)
    state: True = ON, False = OFF
    protocol: "shelly" (Gen2 RPC via ShellyRegistry) | "mqtt" | "webhook"
    """
    device_id: str
    state: bool
    protocol: str = "shelly"  # prefer "shelly" for Gen2 devices via ShellyRegistry
    mqtt_topic: str = ""      # used only when protocol="mqtt"
    webhook_url: str = ""     # used only when protocol="webhook"

    def __str__(self) -> str:
        return f"Switch {self.device_id} {'ON' if self.state else 'OFF'}"


Action = SetSetting | SwitchControl


# ── Rules ─────────────────────────────────────────────────────────────────────

@dataclass
class Rule:
    """
    A single automation rule.

    Conditions are AND'd — all must be True for the rule to fire.
    If any condition references an unavailable signal, the rule is SKIPPED
    (not fired) — prefer doing nothing over doing the wrong thing.

    Actions is a list — a single rule can set multiple things atomically
    (e.g. set capacity_point AND enable grid_charge together).

    simulate: if True, log what would happen but don't publish MQTT.
    """
    name: str
    conditions: list[Condition]
    actions: list[Action]
    priority: int = 100          # lower = evaluated first
    enabled: bool = True
    simulate: bool = True        # default True — installer must explicitly enable
    description: str = ""

    def evaluate(self, snapshot: SignalSnapshot) -> Optional[bool]:
        """
        Returns True if all conditions met, False if any failed,
        None if any signal was unavailable (rule skipped).
        """
        for cond in self.conditions:
            result = cond.evaluate(snapshot)
            if result is None:
                log.info(f"Rule '{self.name}': SKIP — signal '{cond.signal_key}' unavailable")
                return None
            if not result:
                log.debug(f"Rule '{self.name}': NO MATCH — {cond} (value={snapshot.value(cond.signal_key)})")
                return False
        return True

    def __str__(self) -> str:
        conds = " AND ".join(str(c) for c in self.conditions)
        acts  = ", ".join(str(a) for a in self.actions)
        return f"[P{self.priority}] '{self.name}': IF {conds} → {acts}"


# ── RuleSet ───────────────────────────────────────────────────────────────────

@dataclass
class RuleSet:
    """
    An ordered collection of Rules for a single Plant.
    Rules are sorted by priority (ascending) before evaluation.
    """
    name: str
    rules: list[Rule] = field(default_factory=list)

    def sorted_rules(self) -> list[Rule]:
        return sorted([r for r in self.rules if r.enabled], key=lambda r: r.priority)

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)


# ── Engine ────────────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    rule: Optional[Rule]
    matched: bool
    actions: list[Action]
    skipped_rules: list[str]
    snapshot: SignalSnapshot
    simulated: bool = False

    def summary(self) -> str:
        if not self.matched:
            return f"No rule matched. Skipped: {self.skipped_rules}"
        mode = "SIMULATE" if self.simulated else "APPLY"
        return f"[{mode}] Rule '{self.rule.name}' matched → {[str(a) for a in self.actions]}"


class RuleEngine:
    """
    Evaluates a RuleSet against a SignalSnapshot.

    For inverter rules: first-match-wins (mutually exclusive — inverter is in one state).
    For switch rules:   all matching rules fire independently (multiple loads can be on).

    This class handles inverter rules. Switch rules are handled per-device by DivertLogic.
    """

    def __init__(self, ruleset: RuleSet, dry_run: bool = False):
        self.ruleset = ruleset
        self.dry_run = dry_run   # global override: never publish even if rule.simulate=False

    def evaluate(self, snapshot: SignalSnapshot) -> EvalResult:
        skipped = []

        for rule in self.ruleset.sorted_rules():
            result = rule.evaluate(snapshot)

            if result is None:
                skipped.append(rule.name)
                continue

            if result:
                simulated = rule.simulate or self.dry_run
                log.info(f"Rule '{rule.name}' MATCHED — {'SIMULATE' if simulated else 'APPLY'}")
                for action in rule.actions:
                    log.info(f"  → {action}")
                return EvalResult(
                    rule=rule,
                    matched=True,
                    actions=rule.actions,
                    skipped_rules=skipped,
                    snapshot=snapshot,
                    simulated=simulated,
                )

        log.info(f"No rule matched. Skipped ({len(skipped)}): {skipped}")
        return EvalResult(
            rule=None,
            matched=False,
            actions=[],
            skipped_rules=skipped,
            snapshot=snapshot,
        )


# ── Default Rule Set ──────────────────────────────────────────────────────────

def build_default_ruleset() -> RuleSet:
    """
    The 8 default rules that ship with every WattCast install.
    All start in simulate=True — installer must flip to False after verifying.

    Priority order matches rule_engine_design.md.
    Loadshedding rules (7, 8) are included but will be dormant until
    loadshedding_active / loadshedding_starts_in_hours signals are populated.
    """
    rs = RuleSet(name="WattCast Default")

    # ── Rule 1: Solar surplus geyser ─────────────────────────────────────────
    # Highest daily impact. Turn geyser ON when PV surplus > 500W and battery full.
    # Note: use irradiance not pv_power — inverter throttles PV when battery full.
    rs.add(Rule(
        name="Solar surplus geyser ON",
        priority=10,
        description="Run geyser on free solar when battery is full and sun is strong",
        conditions=[
            C("battery_soc",  ">=", 80),
            C("irradiance",   ">=", 600),    # W/m² — confirms sun not just battery
            C("hour",         ">=", 8),
            C("hour",         "<",  17),
        ],
        actions=[
            SwitchControl(device_id="geyser", state=True, protocol="shelly"),
        ],
        simulate=True,
    ))

    # ── Rule 2: Geyser off when no solar ─────────────────────────────────────
    rs.add(Rule(
        name="Geyser off — no solar",
        priority=20,
        description="Turn geyser off when PV is low — don't run on battery or grid",
        conditions=[
            C("pv_power", "<", 200),
        ],
        actions=[
            SwitchControl(device_id="geyser", state=False, protocol="shelly"),
        ],
        simulate=True,
    ))

    # ── Rule 3: Grid outage load shed ─────────────────────────────────────────
    rs.add(Rule(
        name="Grid outage — shed non-essential loads",
        priority=30,
        description="Unplanned outage detected — immediately turn off heavy loads",
        conditions=[
            C("grid_voltage", "<", 180),
        ],
        actions=[
            SwitchControl(device_id="geyser",    state=False, protocol="shelly"),
            SwitchControl(device_id="pool_pump", state=False, protocol="shelly"),
        ],
        simulate=True,
    ))

    # ── Rule 4: Overnight battery protection ──────────────────────────────────
    rs.add(Rule(
        name="Overnight battery protection",
        priority=40,
        description="Raise discharge floor at night to prevent battery draining to shutdown",
        conditions=[
            C("battery_soc", "<",  30),
            C("hour",        ">=", 22),
        ],
        actions=[
            SetSetting("inverter_1/capacity_point_1", 25, "capacity floor"),
            SetSetting("inverter_1/capacity_point_2", 25, "capacity floor"),
            SetSetting("inverter_1/capacity_point_3", 25, "capacity floor"),
            SetSetting("inverter_1/capacity_point_4", 25, "capacity floor"),
            SetSetting("inverter_1/capacity_point_5", 25, "capacity floor"),
            SetSetting("inverter_1/capacity_point_6", 25, "capacity floor"),
        ],
        simulate=True,
    ))

    # ── Rule 5: Winter morning grid top-up ───────────────────────────────────
    rs.add(Rule(
        name="Winter morning grid top-up",
        priority=50,
        description="On cloudy winter mornings, top up from grid before load spike",
        conditions=[
            C("month",               "in", [6, 7, 8]),
            C("hour",                ">=", 6),
            C("hour",                "<",  9),
            C("battery_soc",         "<",  50),
            C("pv_forecast_today",   "<",  5),   # kWh — cloudy day expected
        ],
        actions=[
            SetSetting("inverter_1/grid_charge", "Enabled", "grid charge"),
            SetSetting("inverter_1/capacity_point_1", 60, "winter morning floor"),
        ],
        simulate=True,
    ))

    # ── Rule 6: Solar peak optimiser ─────────────────────────────────────────
    rs.add(Rule(
        name="Solar peak optimiser",
        priority=60,
        description="Lower discharge floor during peak solar to absorb maximum energy",
        conditions=[
            C("hour",     ">=", 10),
            C("hour",     "<",  15),
            C("pv_power", ">",  3000),
        ],
        actions=[
            SetSetting("inverter_1/capacity_point_1", 20, "peak solar floor"),
            SetSetting("inverter_1/capacity_point_2", 20, "peak solar floor"),
        ],
        simulate=True,
    ))

    # ── Rule 7: Pre-loadshedding charge boost (dormant) ───────────────────────
    rs.add(Rule(
        name="Pre-loadshedding charge boost",
        priority=70,
        description="Charge battery before loadshedding slot — dormant until LS returns",
        conditions=[
            C("loadshedding_starts_in_hours", "<",  3),
            C("loadshedding_starts_in_hours", ">",  0),
            C("battery_soc",                  "<",  80),
        ],
        actions=[
            SetSetting("inverter_1/capacity_point_1", 80, "pre-LS floor"),
            SetSetting("inverter_1/grid_charge", "Enabled", "pre-LS grid charge"),
        ],
        simulate=True,
    ))

    # ── Rule 8: Post-loadshedding recovery (dormant) ──────────────────────────
    rs.add(Rule(
        name="Post-loadshedding recovery",
        priority=80,
        description="Restore normal settings after loadshedding ends — dormant until LS returns",
        conditions=[
            C("loadshedding_active", "==", False),
            C("hour",                ">=", 6),
            C("hour",                "<",  18),
        ],
        actions=[
            SetSetting("inverter_1/grid_charge", "Disabled", "restore grid charge"),
            SetSetting("inverter_1/capacity_point_1", 60, "restore floor"),
        ],
        simulate=True,
    ))

    return rs


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Simulate a midday scenario: battery full, sun shining
    snap = SignalSnapshot()
    now = datetime.now()

    def add(key, value, source, unit=""):
        snap.signals[key] = Signal(key=key, value=value, source=source, unit=unit, timestamp=now)

    add("battery_soc",   85,    SignalSource.INVERTER, "%")
    add("pv_power",      4200,  SignalSource.INVERTER, "W")
    add("load_power",    600,   SignalSource.INVERTER, "W")
    add("grid_voltage",  238,   SignalSource.INVERTER, "V")
    add("irradiance",    750,   SignalSource.WEATHER,  "W/m²")
    add("hour",          12,    SignalSource.TIME)
    add("minute",        30,    SignalSource.TIME)
    add("month",         5,     SignalSource.TIME)
    add("day_of_week",   2,     SignalSource.TIME)
    add("pv_forecast_today", 18, SignalSource.FORECAST, "kWh")
    # loadshedding signals deliberately omitted — rules 7/8 will be skipped

    ruleset = build_default_ruleset()
    engine  = RuleEngine(ruleset, dry_run=True)

    print("\n=== WattCast Rule Engine — Test Run ===")
    print(f"Scenario: midday, SoC={snap.value('battery_soc')}%, "
          f"PV={snap.value('pv_power')}W, irradiance={snap.value('irradiance')}W/m²\n")

    for rule in ruleset.sorted_rules():
        print(f"  {rule}")
    print()

    result = engine.evaluate(snap)
    print(f"\nResult: {result.summary()}")
