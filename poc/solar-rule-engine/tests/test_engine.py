"""
tests/test_engine.py — Unit tests for evaluate().

All tests use synthetic data — no MQTT, no HTTP, no real inverter.
Run with: pytest tests/test_engine.py -v
"""

from datetime import datetime

import pytest

from engine import evaluate
from models import ForecastSummary, SystemState, WeatherNow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_CONFIG = {
    "battery": {
        "capacity_kwh": 10.0,
        "min_soc": 20,
        "max_soc": 100,
    },
    "thresholds": {
        "soc_target_rainy_day": 90,
        "soc_target_good_day": 50,
        "soc_target_default": 70,
        "grid_charge_max_a": 50,
        "grid_charge_min_a": 10,
        "grid_charge_off": 0,
        "forecast_good_kwh": 15.0,
        "forecast_poor_kwh": 8.0,
        "rain_probability_high": 0.60,
    },
    "engine": {"dry_run": True},
}


def make_system(soc=70.0, pv=2000.0, load=800.0, grid=0.0, batt=0.0) -> SystemState:
    return SystemState(
        battery_soc=soc,
        pv_power=pv,
        load_power=load,
        grid_power=grid,
        battery_power=batt,
        battery_voltage=52.0,
        battery_current=0.0,
    )


def make_weather(irradiance=600.0, rain_rate=0.0) -> WeatherNow:
    return WeatherNow(
        irradiance=irradiance,
        temperature=25.0,
        humidity=55.0,
        wind_speed=2.0,
        rain_rate=rain_rate,
    )


def make_forecast(
    tomorrow_kwh=15.0,
    today_remaining_kwh=8.0,
    tomorrow_rain=0.1,
    tomorrow_rain_hours=0,
) -> ForecastSummary:
    return ForecastSummary(
        today_remaining_yield_kwh=today_remaining_kwh,
        today_peak_gti=800.0,
        tomorrow_yield_kwh=tomorrow_kwh,
        tomorrow_peak_gti=900.0,
        tomorrow_rain_probability=tomorrow_rain,
        tomorrow_rain_hours=tomorrow_rain_hours,
    )


def cmd_map(commands) -> dict[str, str]:
    """Convert command list to {topic_suffix: value} dict for easy assertions."""
    return {c.topic_suffix: c.value for c in commands}


def action_map(actions) -> dict[str, bool]:
    """Convert switch action list to {switch_name: turn_on} dict."""
    return {a.switch_name: a.turn_on for a in actions}


def evaluate_cmds(system, weather, forecast, config=None, now=None):
    """Helper: call evaluate and return only inverter commands."""
    commands, _ = evaluate(system, weather, forecast, config or BASE_CONFIG, now)
    return commands


def evaluate_actions(system, weather, forecast, config, now=None):
    """Helper: call evaluate and return only switch actions."""
    _, actions = evaluate(system, weather, forecast, config, now)
    return actions


# ---------------------------------------------------------------------------
# Tests — SOC floor (capacity_point_1)
# ---------------------------------------------------------------------------

class TestSOCFloor:
    def test_poor_forecast_raises_floor(self):
        cmds = evaluate_cmds(
            make_system(soc=70),
            make_weather(),
            make_forecast(tomorrow_kwh=5.0),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["capacity_point_1"] == "90"

    def test_good_forecast_lowers_floor(self):
        cmds = evaluate_cmds(
            make_system(soc=70),
            make_weather(),
            make_forecast(tomorrow_kwh=20.0, tomorrow_rain=0.1),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["capacity_point_1"] == "50"

    def test_default_forecast_default_floor(self):
        cmds = evaluate_cmds(
            make_system(soc=70),
            make_weather(),
            make_forecast(tomorrow_kwh=11.0),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["capacity_point_1"] == "70"

    def test_floor_never_below_config_minimum(self):
        config = {**BASE_CONFIG, "battery": {**BASE_CONFIG["battery"], "min_soc": 60}}
        cmds = evaluate_cmds(
            make_system(soc=80),
            make_weather(),
            make_forecast(tomorrow_kwh=20.0, tomorrow_rain=0.1),
            config,
        )
        assert int(cmd_map(cmds)["capacity_point_1"]) >= 60


class TestGridCharge:
    def test_emergency_charge_when_raining_and_soc_low(self):
        cmds = evaluate_cmds(
            make_system(soc=50),
            make_weather(rain_rate=5.0),
            make_forecast(tomorrow_kwh=10.0),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["max_grid_charge_current"] == "50"

    def test_no_grid_charge_when_raining_but_soc_high(self):
        cmds = evaluate_cmds(
            make_system(soc=85),
            make_weather(rain_rate=3.0),
            make_forecast(tomorrow_kwh=10.0),
            BASE_CONFIG,
        )
        charge = int(cmd_map(cmds)["max_grid_charge_current"])
        assert charge < 50

    def test_poor_forecast_charges_when_below_rainy_target(self):
        cmds = evaluate_cmds(
            make_system(soc=55),
            make_weather(rain_rate=0.0),
            make_forecast(tomorrow_kwh=4.0),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["max_grid_charge_current"] == "50"

    def test_poor_forecast_moderate_charge_when_near_target(self):
        cmds = evaluate_cmds(
            make_system(soc=83),
            make_weather(rain_rate=0.0),
            make_forecast(tomorrow_kwh=4.0),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["max_grid_charge_current"] == "10"

    def test_good_forecast_soc_above_floor_no_grid_charge(self):
        cmds = evaluate_cmds(
            make_system(soc=75),
            make_weather(),
            make_forecast(tomorrow_kwh=20.0, tomorrow_rain=0.1),
            BASE_CONFIG,
        )
        assert cmd_map(cmds)["max_grid_charge_current"] == "0"

    def test_afternoon_with_remaining_solar_no_grid_charge(self):
        afternoon = datetime.now().replace(hour=14, minute=0)
        cmds = evaluate_cmds(
            make_system(soc=65),
            make_weather(),
            make_forecast(tomorrow_kwh=11.0, today_remaining_kwh=5.0),
            BASE_CONFIG,
            now=afternoon,
        )
        assert cmd_map(cmds)["max_grid_charge_current"] == "0"

    def test_night_moderate_forecast_moderate_charge(self):
        night = datetime.now().replace(hour=2, minute=0)
        cmds = evaluate_cmds(
            make_system(soc=70),
            make_weather(irradiance=0.0),
            make_forecast(tomorrow_kwh=10.0),
            BASE_CONFIG,
            now=night,
        )
        charge = int(cmd_map(cmds)["max_grid_charge_current"])
        assert charge == 25


class TestCommandStructure:
    def test_always_returns_two_commands(self):
        commands, _ = evaluate(make_system(), make_weather(), make_forecast(), BASE_CONFIG)
        assert len(commands) == 2

    def test_commands_have_reasons(self):
        commands, _ = evaluate(make_system(), make_weather(), make_forecast(), BASE_CONFIG)
        for cmd in commands:
            assert cmd.reason, f"Command {cmd.topic_suffix} has no reason"

    def test_command_values_are_integers_in_string(self):
        commands, _ = evaluate(make_system(), make_weather(), make_forecast(), BASE_CONFIG)
        for cmd in commands:
            val = int(cmd.value)
            assert val >= 0, f"Negative value for {cmd.topic_suffix}: {val}"


# ---------------------------------------------------------------------------
# Switch config helper
# ---------------------------------------------------------------------------

GEYSER_CONFIG = {
    **BASE_CONFIG,
    "switches": [{
        "name": "geyser",
        "type": "shelly",
        "ip": "192.168.1.99",
        "role": "geyser",
        "element_watts": 2000,
        "min_soc_to_run": 50,
        "surplus_margin_watts": 200,
        "window_start_hour": 9,
        "window_end_hour": 16,
        "min_on_minutes": 10,
        "max_daily_minutes": 180,
        "min_off_minutes": 5,
    }],
}


class TestGeyserSwitch:
    def _midday(self):
        return datetime.now().replace(hour=12, minute=0)

    def test_turns_on_with_sufficient_surplus_and_soc(self):
        """Geyser should turn on when surplus > element + margin, SOC ok, in window."""
        actions = evaluate_actions(
            make_system(soc=80, pv=4000, load=800),   # surplus = 3200W > 2200W
            make_weather(rain_rate=0.0),
            make_forecast(),
            GEYSER_CONFIG,
            now=self._midday(),
        )
        assert action_map(actions).get("geyser") is True

    def test_does_not_turn_on_insufficient_surplus(self):
        """Geyser stays off when PV surplus is below element + margin."""
        actions = evaluate_actions(
            make_system(soc=80, pv=1000, load=800),   # surplus = 200W < 2200W
            make_weather(rain_rate=0.0),
            make_forecast(),
            GEYSER_CONFIG,
            now=self._midday(),
        )
        assert action_map(actions).get("geyser") is not True

    def test_does_not_turn_on_low_soc(self):
        """Geyser stays off when battery SOC is below min_soc_to_run."""
        actions = evaluate_actions(
            make_system(soc=40, pv=4000, load=800),   # SOC 40% < min 50%
            make_weather(rain_rate=0.0),
            make_forecast(),
            GEYSER_CONFIG,
            now=self._midday(),
        )
        assert action_map(actions).get("geyser") is not True

    def test_turns_off_when_raining(self):
        """Geyser should turn off when it's raining (surplus unreliable)."""
        actions = evaluate_actions(
            make_system(soc=80, pv=4000, load=800),
            make_weather(rain_rate=5.0),               # raining
            make_forecast(),
            GEYSER_CONFIG,
            now=self._midday(),
        )
        assert action_map(actions).get("geyser") is False

    def test_turns_off_outside_window(self):
        """Geyser should turn off outside the allowed time window."""
        evening = datetime.now().replace(hour=19, minute=0)
        actions = evaluate_actions(
            make_system(soc=80, pv=2000, load=500),
            make_weather(rain_rate=0.0),
            make_forecast(),
            GEYSER_CONFIG,
            now=evening,
        )
        assert action_map(actions).get("geyser") is False

    def test_no_switches_when_no_switch_config(self):
        """With no switches configured, engine returns empty switch actions."""
        _, actions = evaluate(make_system(), make_weather(), make_forecast(), BASE_CONFIG)
        assert actions == []
