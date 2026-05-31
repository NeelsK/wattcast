"""
localbus/drivers/growatt_registers.py — Growatt SPH series (hybrid) register map.

Covers: SPH3000, SPH4000, SPH5000, SPH6000, SPH8000, SPH10000 (single-phase hybrid).
Protocol: Modbus RTU over RS485.
Connection: RS485-1 port (pins 4 & 5 of RJ45 connector on SPH inverters).

CRITICAL DIFFERENCE FROM SUNSYNK/DEYE:
  Growatt uses TWO distinct register types that require different Modbus function codes:

  - INPUT REGISTERS (Function Code 04): Read-only telemetry
    Battery SOC/voltage/current, PV power, grid power, load power,
    temperatures, daily energy totals. Range: 0-124, 1000-1124, 1125-1249.

  - HOLDING REGISTERS (Function Code 03): Read/write control
    Work mode (priority), charge/discharge limits, time-of-use configuration.
    Range: 0-124, 1000-1124.

  The driver MUST use FC04 for telemetry reads and FC03 for control reads/writes.
  Using FC03 on a telemetry address will return wrong data. This is the most
  common mistake when implementing Growatt Modbus.

DEFAULT SETTINGS:
  Baud: 9600, 8N1, Slave address: 1 (configurable via inverter LCD menu).

WORK MODES (Growatt calls them Priority Modes):
  Growatt does not have a single "work_mode" register. Instead it uses three
  independent enable registers for "Load First", "Battery First", and "Grid First".
  Only one should be active at a time. The driver handles this by writing to the
  correct enable register and disabling the others.

SOURCES:
  - Growatt Modbus RTU Protocol V3.14 (official PDF)
  - 8none1/growatt_sph_nodered (registers.md — practical community map)
  - enide-electronics/growatt-sph-spa-esp8266 (REGISTERS.md)
  - muppet3000/homeassistant-growatt_local (HA local Modbus integration)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Optional


class FunctionCode(IntEnum):
    """Modbus function codes used by Growatt."""
    READ_HOLDING = 3    # FC03 — control/config registers
    READ_INPUT = 4      # FC04 — telemetry/measurement registers


@dataclass(frozen=True)
class GrowattRegister:
    """A single Growatt Modbus register."""

    address: int
    function_code: FunctionCode   # FC03 (holding) or FC04 (input) — CRITICAL
    canonical_key: str
    unit: str = ""
    scale: float = 1.0
    signed: bool = False
    words: int = 1                # 1 = 16-bit, 2 = 32-bit (H word first, then L word)
    description: str = ""
    writable: bool = False        # only holding registers (FC03) can be writable
    decoder: Optional[Callable] = None
    encoder: Optional[Callable] = None

    def decode(self, raw_words: list[int]) -> float | int | str:
        if self.decoder is not None:
            return self.decoder(raw_words)
        # Growatt 32-bit: HIGH word first, then LOW word (opposite of Sunsynk)
        if self.words == 2:
            value = (raw_words[0] << 16) | raw_words[1]
            if self.signed and value & 0x80000000:
                value -= 1 << 32
        else:
            value = raw_words[0]
            if self.signed and value & 0x8000:
                value -= 1 << 16
        return value * self.scale if self.scale != 1.0 else value

    def encode(self, value: int | float | str) -> int:
        if self.encoder is not None:
            return self.encoder(value)
        return int(round(value / self.scale)) if self.scale != 1.0 else int(value)


# ---------------------------------------------------------------------------
# Work mode codec
# Growatt priority modes map to WattCast canonical work_mode labels.
# The driver must translate by enabling/disabling the correct FC03 registers.
# ---------------------------------------------------------------------------

# Growatt priority mode → canonical work_mode label
PRIORITY_TO_MODE: dict[str, str] = {
    "load_first": "Zero Export to Load",    # Closest equivalent: loads are priority
    "battery_first": "Limited to Load",     # Battery charges first, limited to local load
    "grid_first": "Selling First",          # Grid takes priority (sell/import as needed)
}

MODE_TO_PRIORITY: dict[str, str] = {v: k for k, v in PRIORITY_TO_MODE.items()}

# Priority mode enable register addresses (FC03, holding)
PRIORITY_ENABLE_REGISTERS: dict[str, int] = {
    "load_first": 1082,
    "battery_first": 1102,
    "grid_first": 1063,
}


def _decode_work_mode_from_registers(load_first: int, battery_first: int, grid_first: int) -> str:
    """Determine active work mode from the three priority enable registers."""
    if load_first:
        return "Zero Export to Load"
    if battery_first:
        return "Limited to Load"
    if grid_first:
        return "Selling First"
    return "Unknown"


# ---------------------------------------------------------------------------
# Register map — INPUT REGISTERS (FC04, read-only telemetry)
# ---------------------------------------------------------------------------

INPUT_REGISTERS: dict[str, GrowattRegister] = {

    # --- Battery --------------------------------------------------------
    "battery_soc": GrowattRegister(
        address=103, function_code=FunctionCode.READ_INPUT,
        canonical_key="battery_soc", unit="%",
        description="Battery state of charge (0–100).",
    ),
    "battery_voltage": GrowattRegister(
        address=13, function_code=FunctionCode.READ_INPUT,
        canonical_key="battery_voltage", unit="V", scale=0.01,
        description="Battery voltage.",
    ),
    "battery_current": GrowattRegister(
        address=14, function_code=FunctionCode.READ_INPUT,
        canonical_key="battery_current", unit="A", scale=0.01, signed=True,
        description="Battery current. Positive = charging, negative = discharging.",
    ),
    "battery_power": GrowattRegister(
        address=15, function_code=FunctionCode.READ_INPUT,
        canonical_key="battery_power", unit="W", signed=True, words=2,
        description="Battery power (32-bit, H word first). Positive = charging.",
    ),
    "battery_temp": GrowattRegister(
        address=18, function_code=FunctionCode.READ_INPUT,
        canonical_key="battery_temp", unit="°C", scale=0.1,
        description="Battery/BMS temperature.",
    ),

    # --- PV (solar) -----------------------------------------------------
    "pv1_voltage": GrowattRegister(
        address=3, function_code=FunctionCode.READ_INPUT,
        canonical_key="pv1_voltage", unit="V", scale=0.1,
    ),
    "pv1_current": GrowattRegister(
        address=4, function_code=FunctionCode.READ_INPUT,
        canonical_key="pv1_current", unit="A", scale=0.1,
    ),
    "pv1_power": GrowattRegister(
        address=5, function_code=FunctionCode.READ_INPUT,
        canonical_key="pv1_power", unit="W", words=2,
        description="PV1 power (32-bit, H first).",
    ),
    "pv2_voltage": GrowattRegister(
        address=7, function_code=FunctionCode.READ_INPUT,
        canonical_key="pv2_voltage", unit="V", scale=0.1,
    ),
    "pv2_current": GrowattRegister(
        address=8, function_code=FunctionCode.READ_INPUT,
        canonical_key="pv2_current", unit="A", scale=0.1,
    ),
    "pv2_power": GrowattRegister(
        address=9, function_code=FunctionCode.READ_INPUT,
        canonical_key="pv2_power", unit="W", words=2,
        description="PV2 power (32-bit, H first).",
    ),

    # --- Grid -----------------------------------------------------------
    "grid_voltage": GrowattRegister(
        address=38, function_code=FunctionCode.READ_INPUT,
        canonical_key="grid_voltage", unit="V", scale=0.1,
        description="Grid/output AC voltage (phase 1).",
    ),
    "grid_frequency": GrowattRegister(
        address=37, function_code=FunctionCode.READ_INPUT,
        canonical_key="grid_frequency", unit="Hz", scale=0.01,
    ),
    "grid_power": GrowattRegister(
        address=1021, function_code=FunctionCode.READ_INPUT,
        canonical_key="grid_power", unit="W", signed=True, words=2,
        description="Grid power (32-bit, H first). Positive = importing, negative = exporting. "
                    "Address 1021 is from the 1000-series extended register block — verify.",
    ),

    # --- Load -----------------------------------------------------------
    "load_power": GrowattRegister(
        address=1, function_code=FunctionCode.READ_INPUT,
        canonical_key="load_power", unit="W", words=2,
        description="Output/load power (32-bit, H first). "
                    "Address 1 is the inverter output power register.",
    ),
    "load_voltage": GrowattRegister(
        address=38, function_code=FunctionCode.READ_INPUT,
        canonical_key="load_voltage", unit="V", scale=0.1,
        description="AC output voltage (same as grid_voltage for grid-tied hybrid).",
    ),

    # --- Inverter -------------------------------------------------------
    "inverter_temp": GrowattRegister(
        address=93, function_code=FunctionCode.READ_INPUT,
        canonical_key="inverter_temp", unit="°C", scale=0.1,
        description="Inverter internal temperature.",
    ),

    # --- Daily energy totals (all 32-bit, H word first) ----------------
    "day_pv_energy": GrowattRegister(
        address=68, function_code=FunctionCode.READ_INPUT,
        canonical_key="day_pv_energy", unit="kWh", scale=0.1, words=2,
    ),
    "day_load_energy": GrowattRegister(
        address=70, function_code=FunctionCode.READ_INPUT,
        canonical_key="day_load_energy", unit="kWh", scale=0.1, words=2,
    ),
    "day_battery_charge": GrowattRegister(
        address=72, function_code=FunctionCode.READ_INPUT,
        canonical_key="day_battery_charge", unit="kWh", scale=0.1, words=2,
    ),
    "day_battery_discharge": GrowattRegister(
        address=74, function_code=FunctionCode.READ_INPUT,
        canonical_key="day_battery_discharge", unit="kWh", scale=0.1, words=2,
    ),
    # Grid import/export daily totals — address range 1000+ on SPH; verify.
    "day_grid_import": GrowattRegister(
        address=1046, function_code=FunctionCode.READ_INPUT,
        canonical_key="day_grid_import", unit="kWh", scale=0.1, words=2,
        description="Daily grid import energy. Address from extended register block — VERIFY.",
    ),
    "day_grid_export": GrowattRegister(
        address=1048, function_code=FunctionCode.READ_INPUT,
        canonical_key="day_grid_export", unit="kWh", scale=0.1, words=2,
        description="Daily grid export energy. Address from extended register block — VERIFY.",
    ),
}

# ---------------------------------------------------------------------------
# Register map — HOLDING REGISTERS (FC03, read/write control)
# ---------------------------------------------------------------------------

HOLDING_REGISTERS: dict[str, GrowattRegister] = {

    # --- Work mode (priority) control -----------------------------------
    # Growatt uses separate enable bits per priority, not a single mode register.
    # The driver handles work_mode by reading/writing these three together.
    # See GrowattModbusDriver.read_work_mode() and write_work_mode().

    "load_first_enable": GrowattRegister(
        address=1082, function_code=FunctionCode.READ_HOLDING,
        canonical_key="load_first_enable",
        description="Load First mode enable. 1=active, 0=inactive. "
                    "Disable battery_first and grid_first when setting this.",
        writable=True,
    ),
    "battery_first_enable": GrowattRegister(
        address=1102, function_code=FunctionCode.READ_HOLDING,
        canonical_key="battery_first_enable",
        description="Battery First mode enable. 1=active, 0=inactive.",
        writable=True,
    ),
    "grid_first_enable": GrowattRegister(
        address=1063, function_code=FunctionCode.READ_HOLDING,
        canonical_key="grid_first_enable",
        description="Grid First mode enable. 1=active, 0=inactive.",
        writable=True,
    ),

    # --- Battery charge/discharge limits --------------------------------
    "battery_min_soc": GrowattRegister(
        address=1070, function_code=FunctionCode.READ_HOLDING,
        canonical_key="battery_min_soc", unit="%",
        description="Minimum battery SOC for Grid First discharge stop. "
                    "Verify — Growatt uses this differently to Sunsynk: it controls when "
                    "the inverter stops discharging in Grid First mode.",
        writable=True,
    ),
    "battery_max_charge_current": GrowattRegister(
        address=1090, function_code=FunctionCode.READ_HOLDING,
        canonical_key="battery_max_charge_current", unit="%",
        description="Battery charge power rate (% of max, not Amps). "
                    "NOTE: Growatt uses percentage, not absolute current. "
                    "The driver may need to convert from Amps to % for this inverter.",
        writable=True,
    ),
    "battery_max_discharge_current": GrowattRegister(
        address=1089, function_code=FunctionCode.READ_HOLDING,
        canonical_key="battery_max_discharge_current", unit="%",
        description="Battery discharge power rate (% of max, not Amps).",
        writable=True,
    ),

    # Battery First stop SOC (max charge target)
    "battery_charge_stop_soc": GrowattRegister(
        address=1091, function_code=FunctionCode.READ_HOLDING,
        canonical_key="battery_charge_stop_soc", unit="%",
        description="SOC at which Battery First mode stops charging. "
                    "Not a canonical WattCast key — handled separately by the driver.",
        writable=True,
    ),

    # --- Time-of-use slots (Battery First) ------------------------------
    # Growatt TOU is different: separate start/stop hour per slot, not HHMM.
    # These are stored as hour integers (0-23), not HHMM.
    # The driver converts WattCast HHMM format to/from Growatt hour format.
    "tou_time_1": GrowattRegister(
        address=1105, function_code=FunctionCode.READ_HOLDING,
        canonical_key="tou_time_1",
        description="Battery First TOU slot 1 start hour (0-23). "
                    "WattCast stores HHMM; driver converts. VERIFY address.",
        writable=True,
    ),
    "tou_time_2": GrowattRegister(
        address=1109, function_code=FunctionCode.READ_HOLDING,
        canonical_key="tou_time_2",
        description="Battery First TOU slot 2 start hour.",
        writable=True,
    ),
    "tou_time_3": GrowattRegister(
        address=1113, function_code=FunctionCode.READ_HOLDING,
        canonical_key="tou_time_3",
        description="Battery First TOU slot 3 start hour.",
        writable=True,
    ),
    "tou_soc_1": GrowattRegister(
        address=1091, function_code=FunctionCode.READ_HOLDING,
        canonical_key="tou_soc_1", unit="%",
        description="Battery First slot 1 charge target SOC. "
                    "Growatt shares this with the general battery_charge_stop_soc. "
                    "Per-slot SOC targets may not be supported — verify.",
        writable=True,
    ),
}

# Combined lookup — telemetry + control
ALL_REGISTERS: dict[str, GrowattRegister] = {**INPUT_REGISTERS, **HOLDING_REGISTERS}

# Keys where Growatt uses % rate instead of Amps — caller should be aware
PERCENT_RATE_KEYS = frozenset({"battery_max_charge_current", "battery_max_discharge_current"})

# Keys not supported on Growatt SPH (vs Sunsynk/Deye)
UNSUPPORTED_KEYS = frozenset({
    "tou_time_4", "tou_time_5", "tou_time_6",  # Growatt SPH has 3 TOU slots, not 6
    "tou_soc_2", "tou_soc_3", "tou_soc_4", "tou_soc_5", "tou_soc_6",
})


def get(key: str) -> GrowattRegister:
    if key in UNSUPPORTED_KEYS:
        raise KeyError(
            f"'{key}' is not supported on Growatt SPH series. "
            f"Growatt has 3 TOU slots (not 6) and different SOC-per-slot behaviour."
        )
    try:
        return ALL_REGISTERS[key]
    except KeyError:
        suggestions = [k for k in ALL_REGISTERS if key.lower() in k.lower()]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise KeyError(f"No Growatt register for canonical key '{key}'.{hint}") from None


def input_registers() -> dict[str, GrowattRegister]:
    """Return only FC04 input (telemetry) registers."""
    return {k: v for k, v in ALL_REGISTERS.items()
            if v.function_code == FunctionCode.READ_INPUT}


def holding_registers() -> dict[str, GrowattRegister]:
    """Return only FC03 holding (control) registers."""
    return {k: v for k, v in ALL_REGISTERS.items()
            if v.function_code == FunctionCode.READ_HOLDING}


def writable_registers() -> dict[str, GrowattRegister]:
    return {k: v for k, v in ALL_REGISTERS.items() if v.writable}
