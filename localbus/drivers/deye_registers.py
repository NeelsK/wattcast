"""
localbus/drivers/deye_registers.py — Deye SUN-5K-SG03LP1 (single-phase hybrid) register map.

Deye and Sunsynk are manufactured by the same parent company (Ningbo Deye Inverter
Technology Co., Ltd.) and share identical hardware and Modbus register maps for
equivalent models. This file documents the Deye-specific addresses as verified by:

  - kbialek/deye-inverter-mqtt (actively maintained community register map)
  - StephanJoubert/home_assistant_solarman (deye_hybrid.yaml)
  - kellerza/sunsynk (cross-reference)
  - DIY Solar Forum Deye Modbus threads

CONNECTION: RS485 via the BMS 485/CAN RJ45 connector on the inverter.
  Baud: 9600, 8N1, Slave address: 1 (default)
  Function code: 04 (read input/holding registers)

IMPORTANT: Some register addresses differ from Sunsynk despite identical hardware.
  Battery registers in particular sit at higher addresses (580s) on Deye firmware
  vs the 180s on Sunsynk. Always verify with mbpoll before enabling writes.

WRITABLE REGISTERS: Test all writes with dry_run=True first. Register 1100
  (Modbus write enable) may need to be set to 1 before writes are accepted on
  some Deye firmware versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Register:
    """A single Modbus register (or 32-bit pair)."""

    address: int
    canonical_key: str
    unit: str = ""
    scale: float = 1.0
    signed: bool = False
    words: int = 1              # 1 = 16-bit, 2 = 32-bit (low word first)
    description: str = ""
    writable: bool = False
    decoder: Optional[Callable] = None
    encoder: Optional[Callable] = None

    def decode(self, raw_words: list[int]) -> float | int | str:
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
        if self.encoder is not None:
            return self.encoder(value)
        return int(round(value / self.scale)) if self.scale != 1.0 else int(value)


# ---------------------------------------------------------------------------
# Work mode codec (Deye uses same labels as Sunsynk)
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
            raise ValueError(f"Unknown work mode '{value}'. Valid: {list(WORK_MODES_REVERSE)}")
        return WORK_MODES_REVERSE[value]
    return int(value)


# ---------------------------------------------------------------------------
# Register map
# NOTE: Deye battery registers are at 580-591, NOT 182-191 as on Sunsynk.
# PV, grid, load, and daily energy registers largely match Sunsynk.
# ---------------------------------------------------------------------------

REGISTERS: dict[str, Register] = {

    # --- Battery --------------------------------------------------------
    # Deye places battery registers at 580s; Sunsynk uses 180s.
    "battery_soc": Register(
        address=184, canonical_key="battery_soc", unit="%",
        description="Battery state of charge (0–100). "
                    "NOTE: address 184 matches Sunsynk; some Deye firmware reports SOC at 586 instead — verify.",
    ),
    "battery_voltage": Register(
        address=587, canonical_key="battery_voltage", unit="V", scale=0.01,
        description="Battery DC voltage",
    ),
    "battery_current": Register(
        address=591, canonical_key="battery_current", unit="A", scale=0.1, signed=True,
        description="Battery current. Negative = charging, Positive = discharging "
                    "(NOTE: sign convention is REVERSED vs Sunsynk — verify on your unit).",
    ),
    "battery_power": Register(
        address=590, canonical_key="battery_power", unit="W", signed=True,
        description="Battery power. Negative = charging, Positive = discharging.",
    ),
    "battery_temp": Register(
        address=586, canonical_key="battery_temp", unit="°C",
        description="Battery temperature (°C, not scaled). "
                    "May read ~1000 higher on some firmware — verify against BMS display.",
    ),

    # --- PV (solar) — matches Sunsynk addresses -------------------------
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
        address=73, canonical_key="grid_voltage", unit="V", scale=0.1,
        description="Grid L1 voltage",
    ),
    "grid_frequency": Register(
        address=79, canonical_key="grid_frequency", unit="Hz", scale=0.01,
    ),
    "grid_power": Register(
        address=80, canonical_key="grid_power", unit="W", signed=True, words=2,
        description="Grid power (32-bit). Positive = importing, negative = exporting.",
    ),

    # --- Load -----------------------------------------------------------
    "load_power": Register(
        address=178, canonical_key="load_power", unit="W",
        description="Total load power. Verify address — community reports vary between 178 and 643.",
    ),
    "load_voltage": Register(
        address=157, canonical_key="load_voltage", unit="V", scale=0.1,
    ),

    # --- Inverter -------------------------------------------------------
    "inverter_temp": Register(
        address=90, canonical_key="inverter_temp", unit="°C", scale=0.1, signed=True,
        description="Inverter heatsink temperature.",
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
    # CAUTION: some Deye firmware requires register 1100 (Modbus write enable)
    # to be set to 1 before any holding register write is accepted.
    # Test ALL writes with dry_run=True first.

    "work_mode": Register(
        address=142, canonical_key="work_mode",
        description="Work mode. 0=Selling First, 1=Zero Export to Load, "
                    "2=Limited to Load, 3=Zero Export to CT.",
        writable=True,
        decoder=_decode_work_mode,
        encoder=_encode_work_mode,
    ),
    "battery_min_soc": Register(
        address=219, canonical_key="battery_min_soc", unit="%",
        description="Minimum battery SOC before inverter stops discharging. Verify address.",
        writable=True,
    ),
    "battery_max_charge_current": Register(
        address=210, canonical_key="battery_max_charge_current", unit="A",
        description="Maximum battery charge current. Verify address.",
        writable=True,
    ),
    "battery_max_discharge_current": Register(
        address=211, canonical_key="battery_max_discharge_current", unit="A",
        description="Maximum battery discharge current. Verify address.",
        writable=True,
    ),

    # TOU slots — addresses match Sunsynk; verify on your firmware version.
    "tou_time_1": Register(address=250, canonical_key="tou_time_1", writable=True,
                           description="TOU slot 1 start time (HHMM, e.g. 530 = 05:30). VERIFY."),
    "tou_time_2": Register(address=251, canonical_key="tou_time_2", writable=True),
    "tou_time_3": Register(address=252, canonical_key="tou_time_3", writable=True),
    "tou_time_4": Register(address=253, canonical_key="tou_time_4", writable=True),
    "tou_time_5": Register(address=254, canonical_key="tou_time_5", writable=True),
    "tou_time_6": Register(address=255, canonical_key="tou_time_6", writable=True),
    "tou_soc_1": Register(address=268, canonical_key="tou_soc_1", unit="%", writable=True),
    "tou_soc_2": Register(address=269, canonical_key="tou_soc_2", unit="%", writable=True),
    "tou_soc_3": Register(address=270, canonical_key="tou_soc_3", unit="%", writable=True),
    "tou_soc_4": Register(address=271, canonical_key="tou_soc_4", unit="%", writable=True),
    "tou_soc_5": Register(address=272, canonical_key="tou_soc_5", unit="%", writable=True),
    "tou_soc_6": Register(address=273, canonical_key="tou_soc_6", unit="%", writable=True),
}


def get(key: str) -> Register:
    try:
        return REGISTERS[key]
    except KeyError:
        suggestions = [k for k in REGISTERS if key.lower() in k.lower()]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise KeyError(f"No Deye register for canonical key '{key}'.{hint}") from None


def writable_registers() -> dict[str, Register]:
    return {k: v for k, v in REGISTERS.items() if v.writable}
