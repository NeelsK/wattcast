"""
engine.py — Preset-based rule evaluation for solar system control.

The evaluate() function is PURE:
  - Takes system state, weather, forecast, config, and optional current time
  - Evaluates rules in priority order (lowest number first)
  - Returns an EvalResult with the matched rule/preset and reason
  - No I/O, no MQTT, no HTTP, no side effects
  - Fully unit-testable with synthetic inputs

Rule structure (from config):
  rules:
    - name: "Storm Protection"
      priority: 10
      preset: storm_protection
      conditions:
        any:              # groups are OR'd
          - all:          # conditions within a group are AND'd
              - field: rain_rate
                op: ">"
                value: 2.0

Available condition fields:
  battery_soc               — % (0–100)
  pv_power                  — W
  load_power                — W
  net_surplus               — W (pv - load)
  rain_rate                 — mm/h
  rain_probability_tomorrow — % (0–100)
  rain_probability_today    — % (0–100, max hourly)
  forecast_tomorrow_kwh     — kWh
  forecast_today_remaining_kwh — kWh
  irradiance                — W/m²
  irradiance_avg_15min      — W/m²
  cloud_cover_now           — % (0–100)
  loadshedding_active       — 1 (true) or 0 (false)  [stub]
  loadshedding_next_hours   — hours until next slot   [stub]
  hour                      — current hour (0–23)
"""

import logging
from datetime import datetime
from typing import Optional

from models import (
    Condition, ConditionGroup, EvalResult, ForecastSummary,
    Preset, Rule, SystemState, TimeSlot, WeatherNow,
)

logger = logging.getLogger(__name__)

OPERATORS = {
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# ---------------------------------------------------------------------------
# Config parsing helpers
# ---------------------------------------------------------------------------

def _load_presets_from_yaml(config: dict) -> dict[str, Preset]:
    """Parse presets section of config.yaml into Preset objects."""
    presets: dict[str, Preset] = {}
    for name, data in config.get("presets", {}).items():
        raw_slots = data.get("slots", [])
        slots = [
            TimeSlot(
                time=s["time"],
                capacity=int(s["capacity"]),
                grid_charge=bool(s.get("grid_charge", True)),
            )
            for s in raw_slots
        ]
        while len(slots) < 6:
            last = slots[-1]
            slots.append(TimeSlot(time=last.time, capacity=last.capacity, grid_charge=False))
        presets[name] = Preset(
            name=name,
            description=data.get("description", ""),
            slots=slots[:6],
        )
    return presets


def _load_presets_from_db(config: dict) -> dict[str, Preset]:
    """Load presets from MariaDB. Returns empty dict if tables don't exist or are empty."""
    try:
        import pymysql
        import pymysql.cursors
        db = config["mariadb"]
        conn = pymysql.connect(
            host=db["host"], port=db.get("port", 3306),
            user=db["user"], password=db["password"], database=db["database"],
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=3,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.name, p.description,
                           s.slot_num, s.time, s.capacity, s.grid_charge
                    FROM engine_presets p
                    LEFT JOIN engine_preset_slots s ON s.preset_id = p.id
                    ORDER BY p.name, s.slot_num
                """)
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return {}

        # Group by preset name
        presets: dict[str, Preset] = {}
        grouped: dict[str, dict] = {}
        for row in rows:
            name = row["name"]
            if name not in grouped:
                grouped[name] = {"description": row["description"] or "", "slots": []}
            if row["slot_num"] is not None:
                grouped[name]["slots"].append(
                    TimeSlot(
                        time=row["time"],
                        capacity=int(row["capacity"]),
                        grid_charge=bool(row["grid_charge"]),
                    )
                )
        for name, data in grouped.items():
            slots = sorted(data["slots"], key=lambda s: s.time)  # already ordered by slot_num
            presets[name] = Preset(name=name, description=data["description"], slots=slots)
        return presets

    except Exception as e:
        logger.warning("Could not load presets from DB: %s — falling back to yaml", e)
        return {}


def load_presets(config: dict) -> dict[str, Preset]:
    """Load presets from DB first, fall back to yaml config if DB is empty."""
    presets = _load_presets_from_db(config)
    if presets:
        logger.debug("Loaded %d presets from DB", len(presets))
        return presets
    logger.info("No presets in DB — loading from config.yaml")
    return _load_presets_from_yaml(config)


def load_rules(config: dict) -> list[Rule]:
    """Parse rules section of config into Rule objects, sorted by priority."""
    rules: list[Rule] = []
    for r in config.get("rules", []):
        groups: list[ConditionGroup] = []
        for group_conditions in r.get("conditions", {}).get("any", []):
            conditions = [
                Condition(
                    field=c["field"],
                    op=c["op"],
                    value=float(c["value"]),
                )
                for c in group_conditions.get("all", [])
            ]
            groups.append(ConditionGroup(conditions=conditions))
        rules.append(Rule(
            name=r["name"],
            priority=int(r.get("priority", 999)),
            preset=r["preset"],
            groups=groups,
            default=bool(r.get("default", False)),
            description=r.get("description", ""),
        ))
    return sorted(rules, key=lambda r: r.priority)


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _build_context(
    system: SystemState,
    weather: WeatherNow,
    forecast: ForecastSummary,
    now: datetime,
) -> dict[str, float]:
    """Build a flat dict of all available condition fields."""
    return {
        "battery_soc":                  system.battery_soc,
        "pv_power":                     system.pv_power,
        "load_power":                   system.load_power,
        "net_surplus":                  system.net_solar_surplus,
        "rain_rate":                    weather.rain_rate,
        "rain_probability_tomorrow":    forecast.tomorrow_rain_probability * 100,
        "forecast_tomorrow_kwh":        forecast.tomorrow_yield_kwh,
        "forecast_today_remaining_kwh": forecast.today_remaining_yield_kwh,
        "irradiance":                   weather.irradiance,
        "irradiance_avg_15min":         weather.irradiance_avg_15min,
        "cloud_cover_now":              forecast.cloud_cover_now * 100,
        "loadshedding_active":          0.0,   # stub
        "loadshedding_next_hours":      99.0,  # stub
        "hour":                         float(now.hour),
    }


def _evaluate_condition(cond: Condition, ctx: dict[str, float]) -> tuple[bool, str]:
    """Evaluate a single condition. Returns (matched, description)."""
    if cond.field not in ctx:
        logger.warning("Unknown condition field '%s' — treating as False", cond.field)
        return False, f"unknown field '{cond.field}'"
    actual = ctx[cond.field]
    op_fn = OPERATORS.get(cond.op)
    if op_fn is None:
        logger.warning("Unknown operator '%s' — treating as False", cond.op)
        return False, f"unknown op '{cond.op}'"
    result = op_fn(actual, cond.value)
    desc = f"{cond.field}={actual:.1f} {cond.op} {cond.value} → {'✓' if result else '✗'}"
    return result, desc


def _evaluate_group(group: ConditionGroup, ctx: dict[str, float]) -> tuple[bool, list[str]]:
    """Evaluate a condition group (AND of all conditions)."""
    descs = []
    for cond in group.conditions:
        matched, desc = _evaluate_condition(cond, ctx)
        descs.append(desc)
        if not matched:
            return False, descs
    return True, descs


def _evaluate_rule(rule: Rule, ctx: dict[str, float]) -> tuple[bool, str]:
    """
    Evaluate a rule (OR of groups).
    Returns (matched, reason_string).
    A rule with default=True always matches regardless of conditions.
    """
    if rule.default:
        return True, f"default fallback rule '{rule.name}'"

    for i, group in enumerate(rule.groups):
        matched, descs = _evaluate_group(group, ctx)
        if matched:
            reason = f"rule '{rule.name}' (priority {rule.priority}): group {i+1} matched — " + ", ".join(descs)
            return True, reason

    return False, f"rule '{rule.name}' did not match"


# ---------------------------------------------------------------------------
# Main evaluate function
# ---------------------------------------------------------------------------

def evaluate(
    system: SystemState,
    weather: WeatherNow,
    forecast: ForecastSummary,
    config: dict,
    now: Optional[datetime] = None,
) -> EvalResult:
    """
    Evaluate rules and return an EvalResult with the preset to apply.
    Does NOT apply the preset — that is the caller's responsibility.
    """
    if now is None:
        now = datetime.now()

    presets = load_presets(config)
    rules = load_rules(config)
    ctx = _build_context(system, weather, forecast, now)

    logger.info("─── Rule engine eval @ %s ───", now.strftime("%H:%M:%S"))
    logger.info(
        "  SOC=%.0f%%  PV=%.0fW  Load=%.0fW  Surplus=%.0fW  Rain=%.1fmm/h  "
        "Irr=%.0fW/m²  TomorrowFcast=%.1fkWh  RainProb=%.0f%%",
        ctx["battery_soc"], ctx["pv_power"], ctx["load_power"], ctx["net_surplus"],
        ctx["rain_rate"], ctx["irradiance"],
        ctx["forecast_tomorrow_kwh"], ctx["rain_probability_tomorrow"],
    )

    for rule in rules:
        matched, reason = _evaluate_rule(rule, ctx)
        if matched:
            preset_name = rule.preset
            if preset_name not in presets:
                logger.error("Rule '%s' references unknown preset '%s'", rule.name, preset_name)
                continue
            logger.info("  ✓ Matched: %s → preset '%s'", reason, preset_name)
            return EvalResult(
                timestamp=now,
                matched_rule=rule.name,
                matched_preset=preset_name,
                reason=reason,
                inputs=ctx,
            )
        else:
            logger.debug("  ✗ %s", reason)

    # No rule matched — this shouldn't happen if a default rule is defined
    logger.warning("No rule matched — no preset will be applied")
    return EvalResult(
        timestamp=now,
        matched_rule=None,
        matched_preset=None,
        reason="No rule matched",
        inputs=ctx,
    )
