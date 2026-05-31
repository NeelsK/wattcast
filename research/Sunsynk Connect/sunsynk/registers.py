"""
Register map for the Sunsynk 5K-SG04LP1 (single-phase hybrid).

These addresses are sourced from the kellerza/sunsynk community project
(https://github.com/kellerza/sunsynk) which maintains the most up-to-date
register definitions for Sunsynk single-phase hybrids.

CAVEAT: register addresses can shift between firmware revisions, especially
for control/write registers. Always read a register first and confirm the
value matches what the inverter LCD shows before trusting a write.
"""

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Register:
    """A single Modbus holding register (or pair, for 32-bit values)."""

    address: int
    name: str
    unit: str = ""
    scale: float = 1.0
    signed: bool = False
    words: int = 1  # 1 = 16-bit, 2 = 32-bit (low word first)
    description: str = ""
    writable: bool = False
    decoder: Optional[Callable] = None  # custom decoder for enum-like registers

    def decode(self, raw_words: list[int]) -> float | int | str:
        """Convert raw 16-bit words into a useful value."""
        if self.decoder is not None:
            return self.decoder(raw_words)
        if self.words == 2:
            value = (raw_words[1] << 16) | raw_words[0]
            if self.signed and value & 0x80000000:
                value -= 1 << 32
        else:
            value = raw_words[0]
            if self.signed and value & 0x8000:
                value -= 1 << 16
        return value * self.scale if self.scale != 1.0 else value


# --- Work mode decoder ---------------------------------------------------

WORK_MODES = {
    0: "Selling First",
    1: "Zero Export to Load",
    2: "Limited to Load",
    3: "Zero Export to CT",
}


def _decode_work_mode(words: list[int]) -> str:
    return WORK_MODES.get(words[0], f"Unknown ({words[0]})")


# --- Register map --------------------------------------------------------
# Read-only telemetry registers and writable control registers for the
# Sunsynk 5K-SG04LP1. Names are stable; the address column is what may
# shift between firmwares.

REGISTERS: dict[str, Register] = {
    # --- Battery -----------------------------------------------------
    "battery_soc": Register(
        address=184, name="battery_soc", unit="%",
        description="Battery state of charge (0-100)",
    ),
    "battery_voltage": Register(
        address=183, name="battery_voltage", unit="V", scale=0.01,
        description="Battery DC voltage",
    ),
    "battery_current": Register(
        address=191, name="battery_current", unit="A", scale=0.01, signed=True,
        description="Battery current (positive = charging, negative = discharging)",
    ),
    "battery_power": Register(
        address=190, name="battery_power", unit="W", signed=True,
        description="Battery power (positive = charging)",
    ),
    "battery_temp": Register(
        address=182, name="battery_temp", unit="°C", scale=0.1, signed=True,
        description="Battery temperature (raw value is offset by 1000 on some firmwares — verify)",
    ),
    # --- PV (solar) --------------------------------------------------
    "pv1_voltage": Register(
        address=109, name="pv1_voltage", unit="V", scale=0.1,
    ),
    "pv1_current": Register(
        address=110, name="pv1_current", unit="A", scale=0.1,
    ),
    "pv1_power": Register(
        address=186, name="pv1_power", unit="W",
    ),
    "pv2_voltage": Register(
        address=111, name="pv2_voltage", unit="V", scale=0.1,
    ),
    "pv2_current": Register(
        address=112, name="pv2_current", unit="A", scale=0.1,
    ),
    "pv2_power": Register(
        address=187, name="pv2_power", unit="W",
    ),
    # --- Grid --------------------------------------------------------
    "grid_voltage": Register(
        address=150, name="grid_voltage", unit="V", scale=0.1,
    ),
    "grid_frequency": Register(
        address=79, name="grid_frequency", unit="Hz", scale=0.01,
    ),
    "grid_power": Register(
        address=169, name="grid_power", unit="W", signed=True,
        description="Grid power (positive = importing, negative = exporting)",
    ),
    # --- Load --------------------------------------------------------
    "load_power": Register(
        address=178, name="load_power", unit="W",
        description="Total load power",
    ),
    "load_voltage": Register(
        address=157, name="load_voltage", unit="V", scale=0.1,
    ),
    # --- Inverter ----------------------------------------------------
    "inverter_temp": Register(
        address=90, name="inverter_temp", unit="°C", scale=0.1, signed=True,
        description="Inverter heatsink temperature (offset 1000 on some firmwares)",
    ),
    # --- Daily energy totals -----------------------------------------
    "day_pv_energy": Register(
        address=108, name="day_pv_energy", unit="kWh", scale=0.1,
    ),
    "day_battery_charge": Register(
        address=70, name="day_battery_charge", unit="kWh", scale=0.1,
    ),
    "day_battery_discharge": Register(
        address=71, name="day_battery_discharge", unit="kWh", scale=0.1,
    ),
    "day_grid_import": Register(
        address=76, name="day_grid_import", unit="kWh", scale=0.1,
    ),
    "day_grid_export": Register(
        address=77, name="day_grid_export", unit="kWh", scale=0.1,
    ),
    "day_load_energy": Register(
        address=84, name="day_load_energy", unit="kWh", scale=0.1,
    ),
    # --- Control / writable -----------------------------------------
    # CAUTION: these write to live inverter behaviour. Test in dry-run mode
    # (read the value, confirm against the LCD) before doing real writes.
    "work_mode": Register(
        address=142, name="work_mode",
        description="Work mode: 0=Selling First, 1=Zero Export to Load, 2=Limited to Load, 3=Zero Export to CT",
        writable=True,
        decoder=_decode_work_mode,
    ),
    "battery_min_soc": Register(
        address=219, name="battery_min_soc", unit="%",
        description="Minimum battery SOC the inverter is allowed to discharge to",
        writable=True,
    ),
    "battery_max_charge_current": Register(
        address=210, name="battery_max_charge_current", unit="A",
        description="Max battery charge current",
        writable=True,
    ),
    "battery_max_discharge_current": Register(
        address=211, name="battery_max_discharge_current", unit="A",
        description="Max battery discharge current",
        writable=True,
    ),
    # Time-of-use (6 slots). Addresses below are from the kellerza/sunsynk
    # community map. *** VERIFY BEFORE WRITING *** — slot times are stored
    # as HHMM integers (e.g. 530 means 05:30, 1430 means 14:30).
    "tou_time_1": Register(address=250, name="tou_time_1", writable=True,
                          description="TOU slot 1 start time (HHMM, e.g. 530 = 05:30)"),
    "tou_time_2": Register(address=251, name="tou_time_2", writable=True),
    "tou_time_3": Register(address=252, name="tou_time_3", writable=True),
    "tou_time_4": Register(address=253, name="tou_time_4", writable=True),
    "tou_time_5": Register(address=254, name="tou_time_5", writable=True),
    "tou_time_6": Register(address=255, name="tou_time_6", writable=True),
    # TOU target SOC (one per slot). Some firmwares place these at 268–273.
    "tou_soc_1": Register(address=268, name="tou_soc_1", unit="%", writable=True),
    "tou_soc_2": Register(address=269, name="tou_soc_2", unit="%", writable=True),
    "tou_soc_3": Register(address=270, name="tou_soc_3", unit="%", writable=True),
    "tou_soc_4": Register(address=271, name="tou_soc_4", unit="%", writable=True),
    "tou_soc_5": Register(address=272, name="tou_soc_5", unit="%", writable=True),
    "tou_soc_6": Register(address=273, name="tou_soc_6", unit="%", writable=True),
}


def get(name: str) -> Register:
    """Look up a register by name, raising KeyError with a helpful message."""
    try:
        return REGISTERS[name]
    except KeyError as e:
        suggestions = [n for n in REGISTERS if name.lower() in n.lower()]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise KeyError(f"Unknown register '{name}'.{hint}") from e
