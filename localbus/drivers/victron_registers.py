"""
localbus/drivers/victron_registers.py — Victron Energy Modbus TCP register map.

Victron systems use Modbus TCP (not RS485) via a Venus OS GX device (Cerbo GX,
Venus GX, Raspberry Pi running Venus OS). The GX device acts as a gateway,
aggregating data from all connected Victron equipment.

ARCHITECTURE: Unlike Sunsynk/Deye (one device, one register space), Victron
uses multiple Unit IDs — one per physical device type:

  Unit ID 100 — System (aggregated totals: battery SOC, grid power, AC load)
  Unit ID 246 — VE.Bus device (MultiPlus/Quattro inverter/charger)
  Unit ID 239 — Solar charger #1 (SmartSolar MPPT); 240 = second MPPT, etc.
  Unit ID 257 — Battery monitor #1 (BMV-712 or BMS); 258 = second monitor

Unit IDs are dynamically assigned and CAN change if devices are removed/re-added.
Before deploying, verify active unit IDs at:
  GX device → Settings → Services → Modbus/TCP → Available services

CONNECTION: Modbus TCP on port 502 to the GX device's local IP address.
  Enable at: GX device → Settings → Services → Modbus/TCP → Enabled: On

OFFICIAL REGISTER LIST:
  https://github.com/victronenergy/dbus_modbustcp
  Download: CCGX-Modbus-TCP-register-list-<version>.xlsx

WRITABLE REGISTERS: ESS grid setpoint registers (37, 40, 41) must be written
  every 60 seconds or the Multi reverts to Passthru. Battery minimum SOC is
  NOT directly writable via Modbus — it is configured in the ESS settings on
  the GX device. The closest equivalent is the BatteryLife sustain SOC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Unit IDs — Victron's device-type addressing
# ---------------------------------------------------------------------------

UNIT_SYSTEM = 100       # com.victronenergy.system — aggregated totals
UNIT_VEBUS = 246        # com.victronenergy.vebus — MultiPlus/Quattro
UNIT_SOLARCHARGER = 239 # com.victronenergy.solarcharger — first MPPT
UNIT_BATTERY = 257      # com.victronenergy.battery — BMV or BMS


@dataclass(frozen=True)
class VictronRegister:
    """A single Victron Modbus TCP register."""

    address: int
    unit_id: int                # which Modbus unit (device type) this belongs to
    canonical_key: str
    unit: str = ""
    scale: float = 1.0
    signed: bool = False
    words: int = 1
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
# ESS mode codec
# ---------------------------------------------------------------------------

ESS_MODES: dict[int, str] = {
    1: "ESS with Battery Life",
    2: "ESS without Battery Life",
    3: "External Control",
}
ESS_MODES_REVERSE: dict[str, int] = {v: k for k, v in ESS_MODES.items()}

SWITCH_POSITIONS: dict[int, str] = {
    1: "Charger Only",
    2: "Inverter Only",
    3: "On",
    4: "Off",
}


def _decode_ess_mode(words: list[int]) -> str:
    return ESS_MODES.get(words[0], f"Unknown ({words[0]})")


def _encode_ess_mode(value: int | str) -> int:
    if isinstance(value, str):
        if value not in ESS_MODES_REVERSE:
            raise ValueError(f"Unknown ESS mode '{value}'. Valid: {list(ESS_MODES_REVERSE)}")
        return ESS_MODES_REVERSE[value]
    return int(value)


def _decode_switch_position(words: list[int]) -> str:
    return SWITCH_POSITIONS.get(words[0], f"Unknown ({words[0]})")


# ---------------------------------------------------------------------------
# Register map — grouped by unit_id
# ---------------------------------------------------------------------------

REGISTERS: dict[str, VictronRegister] = {

    # ===================================================================
    # SYSTEM (Unit ID 100) — aggregated values across all devices
    # Use these as the primary telemetry source; they aggregate across
    # multiple MPPTs, phases, etc.
    # ===================================================================

    "battery_soc": VictronRegister(
        address=843, unit_id=UNIT_SYSTEM, canonical_key="battery_soc", unit="%",
        description="Battery state of charge (0–100), aggregated from all battery monitors.",
    ),
    "battery_voltage": VictronRegister(
        address=840, unit_id=UNIT_SYSTEM, canonical_key="battery_voltage",
        unit="V", scale=0.1, signed=True,
        description="Battery voltage (system level).",
    ),
    "battery_current": VictronRegister(
        address=841, unit_id=UNIT_SYSTEM, canonical_key="battery_current",
        unit="A", scale=0.1, signed=True,
        description="Battery current. Positive = charging.",
    ),
    "battery_power": VictronRegister(
        address=842, unit_id=UNIT_SYSTEM, canonical_key="battery_power",
        unit="W", signed=True,
        description="Battery power. Positive = charging.",
    ),

    # Grid (system level, L1 single-phase)
    "grid_voltage": VictronRegister(
        address=856, unit_id=UNIT_SYSTEM, canonical_key="grid_voltage",
        unit="V", scale=0.1,
        description="Grid L1 voltage.",
    ),
    "grid_power": VictronRegister(
        address=860, unit_id=UNIT_SYSTEM, canonical_key="grid_power",
        unit="W", signed=True,
        description="Grid power (L1). Positive = importing from grid, negative = exporting.",
    ),

    # AC output / load (system level)
    "load_power": VictronRegister(
        address=817, unit_id=UNIT_SYSTEM, canonical_key="load_power",
        unit="W", signed=True,
        description="Total AC consumption (system load). "
                    "Verify address — official Excel register list is authoritative.",
    ),

    # ===================================================================
    # VE.Bus / MultiPlus (Unit ID 246) — inverter/charger detail
    # ===================================================================

    "grid_frequency": VictronRegister(
        address=9, unit_id=UNIT_VEBUS, canonical_key="grid_frequency",
        unit="Hz", scale=0.01,
        description="AC input (grid) frequency.",
    ),
    "inverter_temp": VictronRegister(
        address=61, unit_id=UNIT_VEBUS, canonical_key="inverter_temp",
        unit="°C",
        description="MultiPlus/Quattro internal temperature. Verify address in official register list.",
    ),
    "work_mode": VictronRegister(
        address=33, unit_id=UNIT_VEBUS, canonical_key="work_mode",
        description="Switch position: 1=Charger Only, 2=Inverter Only, 3=On, 4=Off.",
        writable=True,
        decoder=_decode_switch_position,
    ),

    # ===================================================================
    # Solar Charger / MPPT (Unit ID 239 = first MPPT)
    # If multiple MPPTs: unit_id 239, 240, 241...
    # The driver should aggregate across all MPPTs for pv1_power etc.
    # ===================================================================

    "pv1_power": VictronRegister(
        address=789, unit_id=UNIT_SOLARCHARGER, canonical_key="pv1_power",
        unit="W", scale=10.0,
        description="PV power from MPPT #1 (unit 239). "
                    "For pv1_power, divide raw register value by 10.",
    ),
    "pv1_voltage": VictronRegister(
        address=776, unit_id=UNIT_SOLARCHARGER, canonical_key="pv1_voltage",
        unit="V", scale=0.01,
        description="PV voltage from MPPT #1.",
    ),
    "pv1_current": VictronRegister(
        address=772, unit_id=UNIT_SOLARCHARGER, canonical_key="pv1_current",
        unit="A", scale=0.1,
        description="PV current from MPPT #1.",
    ),
    "day_pv_energy": VictronRegister(
        address=790, unit_id=UNIT_SOLARCHARGER, canonical_key="day_pv_energy",
        unit="kWh", scale=0.1,
        description="Daily yield from MPPT #1 (resets at midnight). "
                    "For multi-MPPT systems, driver should sum across all MPPTs.",
    ),

    # ===================================================================
    # Battery Monitor (Unit ID 257) — BMV-712 or BMS
    # More precise than system-level battery readings for sites with BMV.
    # ===================================================================

    "battery_temp": VictronRegister(
        address=262, unit_id=UNIT_BATTERY, canonical_key="battery_temp",
        unit="°C", scale=0.1,
        description="Battery temperature from BMV or BMS (unit 257). "
                    "Not always available — depends on whether temperature sensor is connected.",
    ),

    # ===================================================================
    # Writable ESS control registers (Unit ID 100)
    # ===================================================================

    # ESS grid setpoint — must be written every 60s or Multi reverts to Passthru.
    # Positive = inverter will import this many watts from grid.
    # Zero = system tries to be grid-neutral.
    # Negative = system will export to grid.
    "grid_setpoint": VictronRegister(
        address=37, unit_id=UNIT_SYSTEM, canonical_key="grid_setpoint",
        unit="W", signed=True,
        description="ESS grid power setpoint (L1). MUST be written every 60s. "
                    "Positive = import, Negative = export, 0 = grid-neutral.",
        writable=True,
    ),

    # NOTE: battery_min_soc is NOT directly writable via Modbus on Victron.
    # It is configured in the ESS assistant settings on the GX device.
    # The closest Modbus-writable equivalent is the BatteryLife SOC sustain level,
    # but this requires DVCC and appropriate ESS configuration.
    # See: https://www.victronenergy.com/live/ess:design-installation-manual

    # Daily energy totals — available via system unit but addresses need
    # verification against the official Excel register list for your Venus version.
    "day_grid_import": VictronRegister(
        address=827, unit_id=UNIT_SYSTEM, canonical_key="day_grid_import",
        unit="kWh", scale=0.1,
        description="Daily grid import energy. Verify address in official register list.",
    ),
    "day_grid_export": VictronRegister(
        address=828, unit_id=UNIT_SYSTEM, canonical_key="day_grid_export",
        unit="kWh", scale=0.1,
        description="Daily grid export energy. Verify address.",
    ),
}

# Registers not available on Victron via Modbus (or require hardware not always present)
UNSUPPORTED_KEYS = frozenset({
    "pv2_power", "pv2_voltage", "pv2_current",  # second MPPT needs separate unit_id handling
    "load_voltage",                               # not exposed as a distinct register
    "battery_max_charge_current",                 # configured via DVCC on GX device, not Modbus
    "battery_max_discharge_current",              # same
    "battery_min_soc",                            # see note above — not directly writable
    "tou_time_1", "tou_time_2", "tou_time_3",
    "tou_time_4", "tou_time_5", "tou_time_6",
    "tou_soc_1",  "tou_soc_2",  "tou_soc_3",
    "tou_soc_4",  "tou_soc_5",  "tou_soc_6",
    "day_battery_charge", "day_battery_discharge",
    "day_load_energy",
})


def get(key: str) -> VictronRegister:
    if key in UNSUPPORTED_KEYS:
        raise KeyError(
            f"'{key}' is not available via Victron Modbus TCP. "
            f"See UNSUPPORTED_KEYS in victron_registers.py for explanation."
        )
    try:
        return REGISTERS[key]
    except KeyError:
        suggestions = [k for k in REGISTERS if key.lower() in k.lower()]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise KeyError(f"No Victron register for canonical key '{key}'.{hint}") from None


def registers_by_unit(unit_id: int) -> dict[str, VictronRegister]:
    """Return all registers for a specific unit ID. Used by driver to batch reads per unit."""
    return {k: v for k, v in REGISTERS.items() if v.unit_id == unit_id}


def writable_registers() -> dict[str, VictronRegister]:
    return {k: v for k, v in REGISTERS.items() if v.writable}
