"""
localbus/drivers/sunsynk_registers.py — Sunsynk 5K-SG04LP1 register map.

Sourced from the kellerza/sunsynk community project.
https://github.com/kellerza/sunsynk

IMPORTANT: Register addresses can shift between firmware revisions,
especially for writable control registers. Before trusting any write:
  1. Read the register and confirm the value matches the inverter LCD.
  2. If a temperature reads ~1023°C, apply the firmware offset: (raw - 1000) / 10.
  3. Edit this file to correct any address or scale that doesn't match.

This file is intentionally kept separate from the driver so it can be
updated (or swapped for a different model's map) without touching driver logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Register:
    """A single Modbus holding register (or pair for 32-bit values)."""

    address: int
    canonical_key: str          # maps to SIGNAL_KEYS or WRITABLE_KEYS in base.py
    unit: str = ""
    scale: float = 1.0
    signed: bool = False
    words: int = 1              # 1 = 16-bit, 2 = 32-bit (low word first)
    description: str = ""
    writable: bool = False
    decoder: Optional[Callable] = None   # custom decoder for enum-like registers
    encoder: Optional[Callable] = None   # custom encoder for enum-like writable registers

    def decode(self, raw_words: list[int]) -> float | int | str:
        """Convert raw 16-bit words to a useful value."""
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

    def encode(self, value: int | float | str) -> int:
        """Convert a canonical value to the raw int to write to the register."""
        if self.encoder is not None:
            return self.encoder(value)
        raw = int(round(value / self.scale)) if self.scale != 1.0 else int(value)
        return raw


# ---------------------------------------------------------------------------
# Work mode codec
# ---------------------------------------------------------------------------

WORK_MODES: dict[int, str] = {
    0: "Selling First",
    1: "Zero Export to Load",
    2: "Limited to Load",
    3: "Zero Export to CT",
}

WORK_MODES_REVERSE: dict[str, int] = {v: k for k, v in WORK_MODES.items()}


def _decode_work_mode(words: list[int]) -> str:
    return WORK_MODES.get(words[0], f"Unknown ({words[0]})")


def _encode_work_mode(value: int | str) -> int:
    if isinstance(value, str):
        if value not in WORK_MODES_REVERSE:
            raise ValueError(
                f"Unknown work mode '{value}'. Valid modes: {list(WORK_MODES_REVERSE)}"
            )
        return WORK_MODES_REVERSE[value]
    return int(value)


# ---------------------------------------------------------------------------
# Register map
# Keyed by canonical_key for easy lookup.
# ---------------------------------------------------------------------------

REGISTERS: dict[str, Register] = {

    # --- Battery --------------------------------------------------------
    "battery_soc": Register(
        address=184, canonical_key="battery_soc", unit="%",
        description="Battery state of charge (0–100)",
    ),
    "battery_voltage": Register(
        address=183, canonical_key="battery_voltage", unit="V", scale=0.01,
        description="Battery DC voltage",
    ),
    "battery_current": Register(
        address=191, canonical_key="battery_current", unit="A", scale=0.01, signed=True,
        description="Battery current (positive = charging, negative = discharging)",
    ),
    "battery_power": Register(
        address=190, canonical_key="battery_power", unit="W", signed=True,
        description="Battery power (positive = charging)",
    ),
    "battery_temp": Register(
        address=182, canonical_key="battery_temp", unit="°C", scale=0.1, signed=True,
        description="Battery temperature. On some firmwares raw is offset by 1000 — verify against LCD.",
    ),

    # --- PV (solar) -----------------------------------------------------
    "pv1_voltage": Register(
        address=109, canonical_key="pv1_voltage", unit="V", scale=0.1,
    ),
    "pv1_current": Register(
        address=110, canonical_key="pv1_current", unit="A", scale=0.1,
    ),
    "pv1_power": Register(
        address=186, canonical_key="pv1_power", unit="W",
    ),
    "pv2_voltage": Register(
        address=111, canonical_key="pv2_voltage", unit="V", scale=0.1,
    ),
    "pv2_current": Register(
        address=112, canonical_key="pv2_current", unit="A", scale=0.1,
    ),
    "pv2_power": Register(
        address=187, canonical_key="pv2_power", unit="W",
    ),

    # --- Grid -----------------------------------------------------------
    "grid_voltage": Register(
        address=150, canonical_key="grid_voltage", unit="V", scale=0.1,
    ),
    "grid_frequency": Register(
        address=79, canonical_key="grid_frequency", unit="Hz", scale=0.01,
    ),
    "grid_power": Register(
        address=169, canonical_key="grid_power", unit="W", signed=True,
        description="Positive = importing from grid, negative = exporting to grid",
    ),

    # --- Load -----------------------------------------------------------
    "load_power": Register(
        address=178, canonical_key="load_power", unit="W",
        description="Total load power",
    ),
    "load_voltage": Register(
        address=157, canonical_key="load_voltage", unit="V", scale=0.1,
    ),

    # --- Inverter -------------------------------------------------------
    "inverter_temp": Register(
        address=90, canonical_key="inverter_temp", unit="°C", scale=0.1, signed=True,
        description="Inverter heatsink temperature. Offset 1000 on some firmwares — verify.",
    ),

    # --- Daily energy totals --------------------------------------------
    "day_pv_energy": Register(
        address=108, canonical_key="day_pv_energy", unit="kWh", scale=0.1,
    ),
    "day_battery_charge": Register(
        address=70, canonical_key="day_battery_charge", unit="kWh", scale=0.1,
    ),
    "day_battery_discharge": Register(
        address=71, canonical_key="day_battery_discharge", unit="kWh", scale=0.1,
    ),
    "day_grid_import": Register(
        address=76, canonical_key="day_grid_import", unit="kWh", scale=0.1,
    ),
    "day_grid_export": Register(
        address=77, canonical_key="day_grid_export", unit="kWh", scale=0.1,
    ),
    "day_load_energy": Register(
        address=84, canonical_key="day_load_energy", unit="kWh", scale=0.1,
    ),

    # --- Writable control registers ------------------------------------
    # CAUTION: test with dry_run=True first. Read each register and confirm
    # the value matches the inverter LCD before enabling live writes.

    "work_mode": Register(
        address=142, canonical_key="work_mode",
        description="Work mode. Read returns a string label; write accepts int (0–3) or string label.",
        writable=True,
        decoder=_decode_work_mode,
        encoder=_encode_work_mode,
    ),
    "battery_min_soc": Register(
        address=219, canonical_key="battery_min_soc", unit="%",
        description="Minimum battery SOC before the inverter stops discharging",
        writable=True,
    ),
    "battery_max_charge_current": Register(
        address=210, canonical_key="battery_max_charge_current", unit="A",
        description="Maximum battery charge current",
        writable=True,
    ),
    "battery_max_discharge_current": Register(
        address=211, canonical_key="battery_max_discharge_current", unit="A",
        description="Maximum battery discharge current",
        writable=True,
    ),

    # Time-of-use slots (6 slots). Times stored as HHMM integers (530 = 05:30).
    # *** VERIFY addresses before writing — can shift between firmwares. ***
    "tou_time_1": Register(address=250, canonical_key="tou_time_1", writable=True,
                           description="TOU slot 1 start time as HHMM (e.g. 530 = 05:30)"),
    "tou_time_2": Register(address=251, canonical_key="tou_time_2", writable=True),
    "tou_time_3": Register(address=252, canonical_key="tou_time_3", writable=True),
    "tou_time_4": Register(address=253, canonical_key="tou_time_4", writable=True),
    "tou_time_5": Register(address=254, canonical_key="tou_time_5", writable=True),
    "tou_time_6": Register(address=255, canonical_key="tou_time_6", writable=True),

    # TOU target SOC per slot. Some firmwares place these at 268–273.
    "tou_soc_1": Register(address=268, canonical_key="tou_soc_1", unit="%", writable=True),
    "tou_soc_2": Register(address=269, canonical_key="tou_soc_2", unit="%", writable=True),
    "tou_soc_3": Register(address=270, canonical_key="tou_soc_3", unit="%", writable=True),
    "tou_soc_4": Register(address=271, canonical_key="tou_soc_4", unit="%", writable=True),
    "tou_soc_5": Register(address=272, canonical_key="tou_soc_5", unit="%", writable=True),
    "tou_soc_6": Register(address=273, canonical_key="tou_soc_6", unit="%", writable=True),
}


def get(key: str) -> Register:
    """Look up a register by canonical key, with helpful suggestions on miss."""
    try:
        return REGISTERS[key]
    except KeyError:
        suggestions = [k for k in REGISTERS if key.lower() in k.lower()]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise KeyError(f"No register for canonical key '{key}'.{hint}") from None


def writable_registers() -> dict[str, Register]:
    """Return only the registers that can be written to."""
    return {k: v for k, v in REGISTERS.items() if v.writable}
