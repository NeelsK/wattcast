"""
models.py — Pure dataclasses representing system state, presets, and rules.
No I/O, no MQTT, no HTTP. Fully serialisable and testable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Live system state
# ---------------------------------------------------------------------------

@dataclass
class SystemState:
    """Live inverter + battery readings from Solar Assistant via MQTT."""
    battery_soc: float          # % state of charge
    pv_power: float             # W — total PV production
    load_power: float           # W — total household load
    grid_power: float           # W — positive = importing, negative = exporting
    battery_power: float        # W — positive = charging, negative = discharging
    battery_voltage: float      # V
    battery_current: float      # A
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_exporting(self) -> bool:
        return self.grid_power < -50  # 50W deadband

    @property
    def is_importing(self) -> bool:
        return self.grid_power > 50

    @property
    def net_solar_surplus(self) -> float:
        """PV minus load. Positive = surplus available."""
        return self.pv_power - self.load_power


@dataclass
class WeatherNow:
    """Current weather station readings from Ecowitt (local or cloud)."""
    irradiance: float           # W/m² — solar irradiance
    temperature: float          # °C
    humidity: float             # %
    wind_speed: float           # m/s
    rain_rate: float            # mm/hr — current rain
    irradiance_avg_15min: float = 0.0   # W/m² — rolling 15-min average
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_raining(self) -> bool:
        return self.rain_rate > 0.2  # mm/hr threshold


@dataclass
class HourlyForecast:
    """Single hour of Open-Meteo forecast data."""
    hour: datetime
    gti_wm2: float              # Global Tilted Irradiance for panel angle — W/m²
    precipitation_mm: float     # mm
    precipitation_probability: float  # 0.0–1.0
    cloud_cover: float          # 0.0–1.0 (converted from %)
    temperature: float          # °C


@dataclass
class ForecastSummary:
    """Derived summary from hourly Open-Meteo data for today and tomorrow."""
    # Today (remaining hours)
    today_remaining_yield_kwh: float    # Estimated remaining PV yield today
    today_peak_gti: float               # Max W/m² expected today

    # Tomorrow (full day)
    tomorrow_yield_kwh: float           # Estimated full-day PV yield tomorrow
    tomorrow_peak_gti: float            # Max W/m² expected tomorrow
    tomorrow_rain_probability: float    # 0.0–1.0 max hourly probability
    tomorrow_rain_hours: int            # Hours with precipitation > 0.5mm

    # Cloud cover (current hour, 0.0–1.0)
    cloud_cover_now: float = 0.0

    fetched_at: datetime = field(default_factory=datetime.now)

    @property
    def age_minutes(self) -> float:
        return (datetime.now() - self.fetched_at).total_seconds() / 60


# ---------------------------------------------------------------------------
# Presets — named inverter configurations
# ---------------------------------------------------------------------------

@dataclass
class TimeSlot:
    """One of 6 TOU time slots on the inverter."""
    time: str           # "HH:MM"
    capacity: int       # SOC discharge floor % (capacity_point_N)
    grid_charge: bool   # whether grid charging is enabled for this slot (charge_point_N)


@dataclass
class Preset:
    """
    A named inverter configuration — 6 TOU time slots.
    Applied atomically: all 18 MQTT topics written in one go.
    """
    name: str
    description: str
    slots: list[TimeSlot]   # exactly 6 entries

    def __post_init__(self):
        if len(self.slots) != 6:
            raise ValueError(f"Preset '{self.name}' must have exactly 6 slots, got {len(self.slots)}")


# ---------------------------------------------------------------------------
# Rules — conditions that select a preset
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    """
    A single comparison: field op value.
    field: one of the available condition fields (see engine.py)
    op: ">" | "<" | ">=" | "<=" | "==" | "!="
    value: float or bool
    """
    field: str
    op: str
    value: float


@dataclass
class ConditionGroup:
    """
    A list of conditions AND'd together.
    Multiple groups in a rule are OR'd.
    """
    conditions: list[Condition]


@dataclass
class Rule:
    """
    A prioritised rule that maps a set of conditions to a preset.
    Rules are evaluated in ascending priority order (lower number = higher priority).
    First matching rule wins.
    """
    name: str
    priority: int
    preset: str             # name of preset to apply
    groups: list[ConditionGroup]  # OR of groups; each group is AND of conditions
    default: bool = False   # if True, matches when no conditions defined (fallback)
    description: str = ""


# ---------------------------------------------------------------------------
# Evaluation result
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Result of a single rule engine evaluation cycle."""
    timestamp: datetime
    matched_rule: Optional[str]         # rule name that matched
    matched_preset: Optional[str]       # preset name to apply
    reason: str                         # human-readable explanation
    preset_applied: bool = False        # was the preset actually written to inverter?
    suppressed: bool = False            # suppressed by 30-min cooldown?
    preset_unchanged: bool = False      # preset same as currently active?
    inputs: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MQTT commands (kept for compatibility with mqtt_client)
# ---------------------------------------------------------------------------

@dataclass
class Command:
    """A single inverter setting change to publish via MQTT."""
    topic_suffix: str    # e.g. "capacity_point_1"
    value: str           # always a string (MQTT payload)
    reason: str

    def __str__(self) -> str:
        return f"[CMD] {self.topic_suffix} = {self.value!r}  ({self.reason})"


@dataclass
class SwitchAction:
    """A desired state change for a named Wi-Fi switch."""
    switch_name: str
    turn_on: bool
    reason: str

    def __str__(self) -> str:
        action = "ON" if self.turn_on else "OFF"
        return f"[SWITCH] {self.switch_name} → {action}  ({self.reason})"
