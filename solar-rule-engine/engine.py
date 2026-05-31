"""
engine.py — Multi-variable decision logic for solar system control.

The evaluate() function is PURE:
  - Takes only dataclasses + config dict + current time
  - Returns a list of Command objects
  - No I/O, no MQTT, no HTTP, no side effects
  - Fully unit-testable with synthetic inputs

Decision hierarchy (later rules can override earlier ones):
  1. Safety floor — always respect absolute SOC minimum
  2. Emergency conditions — override everything (rain now + low SOC)
  3. Forecast-based planning — adjust grid charge based on tomorrow's yield
  4. Time-of-day adjustments — fine-tune within the day
  5. Real-time corrections — respond to actual PV vs forecast divergence

Commands produced:
  - max_grid_charge_current: how hard to charge from grid (0 = off)
  - capacity_point_1: SOC floor the inverter uses for work mode
"""

import logging
from datetime import datetime, time

from models import Command, ForecastSummary, SwitchAction, SystemState, WeatherNow

logger = logging.getLogger(__name__)


def evaluate(
    system: SystemState,
    weather: WeatherNow,
    forecast: ForecastSummary,
    config: dict,
    now: datetime | None = None,
) -> tuple[list[Command], list[SwitchAction]]:
    """
    Evaluate current state and return:
      - list[Command]: inverter setting changes (via Solar Assistant MQTT)
      - list[SwitchAction]: desired switch states (via SwitchManager)

    All thresholds come from config — the logic is threshold-agnostic.
    """
    if now is None:
        now = datetime.now()

    t = config["thresholds"]
    b = config["battery"]

    # Convenience aliases
    soc = system.battery_soc
    tomorrow_kwh = forecast.tomorrow_yield_kwh
    tomorrow_rain = forecast.tomorrow_rain_probability
    raining_now = weather.is_raining
    current_hour = now.hour

    commands: list[Command] = []

    # -----------------------------------------------------------------------
    # STEP 1: Determine the desired SOC target (capacity_point_1)
    # This is the floor the inverter will not discharge below in normal mode.
    # -----------------------------------------------------------------------

    desired_soc_floor: int
    soc_floor_reason: str

    if tomorrow_kwh < t["forecast_poor_kwh"]:
        # Poor solar day tomorrow — protect battery
        desired_soc_floor = t["soc_target_rainy_day"]
        soc_floor_reason = f"poor forecast tomorrow ({tomorrow_kwh:.1f} kWh < {t['forecast_poor_kwh']} kWh threshold)"

    elif tomorrow_kwh > t["forecast_good_kwh"] and tomorrow_rain < t["rain_probability_high"]:
        # Good solar day tomorrow — allow deeper discharge today
        desired_soc_floor = t["soc_target_good_day"]
        soc_floor_reason = f"good forecast tomorrow ({tomorrow_kwh:.1f} kWh, rain {tomorrow_rain*100:.0f}%)"

    else:
        desired_soc_floor = t["soc_target_default"]
        soc_floor_reason = f"mixed forecast ({tomorrow_kwh:.1f} kWh tomorrow)"

    # Hard floor — never go below config minimum regardless of forecast
    desired_soc_floor = max(desired_soc_floor, b["min_soc"])

    commands.append(Command(
        topic_suffix="capacity_point_1",
        value=str(desired_soc_floor),
        reason=f"SOC floor set to {desired_soc_floor}% — {soc_floor_reason}",
    ))

    # -----------------------------------------------------------------------
    # STEP 2: Determine grid charge current
    # Positive = charge from grid; 0 = no grid charging
    # -----------------------------------------------------------------------

    charge_current: int
    charge_reason: str

    # --- Case A: Currently raining AND SOC is low — emergency grid charge ---
    if raining_now and soc < t["soc_target_default"]:
        charge_current = t["grid_charge_max_a"]
        charge_reason = (
            f"raining now + SOC {soc:.0f}% below default target "
            f"{t['soc_target_default']}% — emergency grid charge"
        )

    # --- Case B: Very poor forecast AND SOC not yet at rainy-day target ---
    elif tomorrow_kwh < t["forecast_poor_kwh"] and soc < t["soc_target_rainy_day"]:
        # Scale charge rate: charge harder the further below target we are
        deficit = t["soc_target_rainy_day"] - soc
        if deficit > 20:
            charge_current = t["grid_charge_max_a"]
        elif deficit > 10:
            charge_current = t["grid_charge_max_a"] // 2
        else:
            charge_current = t["grid_charge_min_a"]
        charge_reason = (
            f"poor forecast ({tomorrow_kwh:.1f} kWh) + SOC {soc:.0f}% "
            f"— need to reach {t['soc_target_rainy_day']}% ({deficit:.0f}% deficit)"
        )

    # --- Case C: Good forecast — minimal or no grid charging ---
    elif tomorrow_kwh > t["forecast_good_kwh"] and tomorrow_rain < t["rain_probability_high"]:
        if soc >= desired_soc_floor:
            # Battery above floor on a good day — no grid charging needed
            charge_current = t["grid_charge_off"]
            charge_reason = (
                f"good forecast ({tomorrow_kwh:.1f} kWh) + SOC {soc:.0f}% above floor "
                f"{desired_soc_floor}% — grid charge off"
            )
        else:
            # Still below floor even on good day — light grid charge to get there
            charge_current = t["grid_charge_min_a"]
            charge_reason = (
                f"good forecast but SOC {soc:.0f}% below floor {desired_soc_floor}% "
                f"— light grid charge"
            )

    # --- Case D: Afternoon on a good day — stop grid charging, let solar finish ---
    elif current_hour >= 13 and forecast.today_remaining_yield_kwh > 3.0:
        charge_current = t["grid_charge_off"]
        charge_reason = (
            f"afternoon ({current_hour}:00) with {forecast.today_remaining_yield_kwh:.1f} kWh "
            f"still expected today — grid charge off, let solar top up"
        )

    # --- Case E: Night-time with poor-ish forecast — moderate grid charge ---
    elif not _is_daytime(now) and tomorrow_kwh < t["forecast_good_kwh"]:
        charge_current = int(t["grid_charge_max_a"] * 0.5)
        charge_reason = (
            f"night-time + moderate forecast ({tomorrow_kwh:.1f} kWh) "
            f"— moderate grid charge"
        )

    # --- Default: do nothing ---
    else:
        charge_current = t["grid_charge_off"]
        charge_reason = (
            f"default — SOC {soc:.0f}%, forecast {tomorrow_kwh:.1f} kWh, "
            f"rain {tomorrow_rain*100:.0f}%"
        )

    commands.append(Command(
        topic_suffix="max_grid_charge_current",
        value=str(charge_current),
        reason=charge_reason,
    ))

    # -----------------------------------------------------------------------
    # STEP 3: Switch decisions
    # Each switch in config.switches gets evaluated here.
    # The engine produces SwitchAction objects; the caller (main.py) checks
    # SwitchRunState guards before actually acting.
    # -----------------------------------------------------------------------

    switch_actions: list[SwitchAction] = []

    for sw_cfg in config.get("switches", []):
        action = _evaluate_switch(sw_cfg, system, weather, forecast, t, now)
        if action is not None:
            switch_actions.append(action)

    # -----------------------------------------------------------------------
    # STEP 4: Log decision summary
    # -----------------------------------------------------------------------
    logger.info("─── Engine decision @ %s ───", now.strftime("%H:%M"))
    logger.info(
        "  Inputs: SOC=%.0f%%  PV=%.0fW  Load=%.0fW  Grid=%.0fW  Rain=%s",
        soc, system.pv_power, system.load_power, system.grid_power,
        "yes" if raining_now else "no",
    )
    logger.info(
        "  Forecast: today remaining=%.1f kWh  tomorrow=%.1f kWh  rain=%.0f%%",
        forecast.today_remaining_yield_kwh, tomorrow_kwh, tomorrow_rain * 100,
    )
    for cmd in commands:
        logger.info("  → %s", cmd)
    for action in switch_actions:
        logger.info("  → %s", action)

    return commands, switch_actions


def _evaluate_switch(
    sw_cfg: dict,
    system: SystemState,
    weather: WeatherNow,
    forecast: ForecastSummary,
    thresholds: dict,
    now: datetime,
) -> SwitchAction | None:
    """
    Decide whether a switch should be turned on or off.

    Returns a SwitchAction if the desired state differs from what the
    engine wants, or None if no action is warranted.

    Each switch config can declare a 'role' which selects the decision
    logic. Unknown roles default to no action (safe).

    Supported roles:
        geyser        — heat water using solar surplus
        pool_pump     — run pump during peak solar
        generic_load  — any deferrable load
    """
    role = sw_cfg.get("role", "generic_load")
    name = sw_cfg["name"]

    if role == "geyser":
        return _evaluate_geyser(name, sw_cfg, system, weather, forecast, thresholds, now)
    elif role == "pool_pump":
        return _evaluate_pool_pump(name, sw_cfg, system, weather, forecast, thresholds, now)
    elif role == "generic_load":
        return _evaluate_generic_load(name, sw_cfg, system, thresholds, now)
    else:
        logger.warning("Switch '%s' has unknown role '%s' — no action", name, role)
        return None


def _evaluate_geyser(
    name: str,
    sw_cfg: dict,
    system: SystemState,
    weather: WeatherNow,
    forecast: ForecastSummary,
    thresholds: dict,
    now: datetime,
) -> SwitchAction | None:
    """
    Geyser heating logic — prioritise solar surplus, protect battery.

    Turn ON when:
      - Solar surplus > geyser element wattage (configurable)
      - Battery SOC > minimum SOC for geyser operation
      - Within allowed time window
      - Not raining so hard that we expect no surplus

    Turn OFF when:
      - Surplus drops below threshold
      - SOC falls below floor
      - Outside time window
    """
    # Switch-specific config with sensible geyser defaults
    element_watts = sw_cfg.get("element_watts", 2000)       # geyser element size
    min_soc_to_run = sw_cfg.get("min_soc_to_run", 50)       # don't run below this SOC
    surplus_margin = sw_cfg.get("surplus_margin_watts", 200) # headroom above element watts
    window_start = sw_cfg.get("window_start_hour", 9)        # earliest start
    window_end = sw_cfg.get("window_end_hour", 16)           # latest stop

    current_hour = now.hour
    surplus = system.net_solar_surplus
    soc = system.battery_soc
    required_surplus = element_watts + surplus_margin

    in_window = window_start <= current_hour < window_end

    # Conditions to turn ON
    want_on = (
        in_window
        and surplus > required_surplus
        and soc > min_soc_to_run
        and not weather.is_raining
    )

    # Conditions to turn OFF (any of these)
    want_off = (
        not in_window
        or surplus < (element_watts - surplus_margin)   # clear deficit
        or soc < thresholds["soc_target_good_day"]      # battery needs protecting
        or weather.is_raining
    )

    if want_on:
        return SwitchAction(
            switch_name=name,
            turn_on=True,
            reason=(
                f"solar surplus {surplus:.0f}W > {required_surplus}W required, "
                f"SOC {soc:.0f}% > {min_soc_to_run}%, in window {window_start}–{window_end}h"
            ),
        )
    elif want_off:
        return SwitchAction(
            switch_name=name,
            turn_on=False,
            reason=(
                f"surplus {surplus:.0f}W, SOC {soc:.0f}%, "
                f"window={'yes' if in_window else 'no'}, rain={'yes' if weather.is_raining else 'no'}"
            ),
        )

    return None  # No change warranted


def _evaluate_pool_pump(
    name: str,
    sw_cfg: dict,
    system: SystemState,
    weather: WeatherNow,
    forecast: ForecastSummary,
    thresholds: dict,
    now: datetime,
) -> SwitchAction | None:
    """
    Pool pump logic — run during peak solar hours if surplus exists.
    Simpler than geyser: no element sizing, just run during surplus.
    """
    pump_watts = sw_cfg.get("pump_watts", 750)
    min_soc_to_run = sw_cfg.get("min_soc_to_run", 40)
    window_start = sw_cfg.get("window_start_hour", 10)
    window_end = sw_cfg.get("window_end_hour", 15)

    in_window = window_start <= now.hour < window_end
    surplus = system.net_solar_surplus

    if in_window and surplus > pump_watts and system.battery_soc > min_soc_to_run:
        return SwitchAction(
            switch_name=name,
            turn_on=True,
            reason=f"solar surplus {surplus:.0f}W > {pump_watts}W pump, in window",
        )
    elif not in_window or surplus < (pump_watts * 0.5):
        return SwitchAction(
            switch_name=name,
            turn_on=False,
            reason=f"surplus {surplus:.0f}W or outside window {window_start}–{window_end}h",
        )
    return None


def _evaluate_generic_load(
    name: str,
    sw_cfg: dict,
    system: SystemState,
    thresholds: dict,
    now: datetime,
) -> SwitchAction | None:
    """
    Generic deferrable load: on during surplus within a time window, off otherwise.
    """
    load_watts = sw_cfg.get("load_watts", 500)
    min_soc_to_run = sw_cfg.get("min_soc_to_run", 50)
    window_start = sw_cfg.get("window_start_hour", 9)
    window_end = sw_cfg.get("window_end_hour", 17)

    in_window = window_start <= now.hour < window_end
    surplus = system.net_solar_surplus

    if in_window and surplus > load_watts and system.battery_soc > min_soc_to_run:
        return SwitchAction(switch_name=name, turn_on=True,
            reason=f"surplus {surplus:.0f}W > {load_watts}W load, in window")
    else:
        return SwitchAction(switch_name=name, turn_on=False,
            reason=f"surplus {surplus:.0f}W or outside window")


def _is_daytime(now: datetime) -> bool:
    """Rough daytime check: 07:00 – 18:00 local time."""
    return time(7, 0) <= now.time() <= time(18, 0)
