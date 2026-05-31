"""
localbus/base.py — LocalBus abstract base interface.

Every inverter driver (Sunsynk RS485, Victron VE.Bus, Deye Modbus, etc.)
must implement this interface. The WattCast rule engine and Pi agent talk
only to this interface — they never know which brand is underneath.

Key design decisions:
- read_snapshot() returns a flat dict keyed by the WattCast canonical signal
  names defined in SIGNAL_KEYS. Drivers must map brand-specific register
  names to these canonical names.
- apply_preset() accepts a settings blob (dict of canonical name → value)
  and writes all values atomically (or as close to atomically as the
  hardware allows). Partial writes are logged but do not raise — the driver
  continues applying remaining settings and reports failures at the end.
- All methods are synchronous. The Pi agent runs in a single thread per
  plant and calls these from its own loop. Thread safety is the caller's
  responsibility if multiple threads share an instance.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("localbus")


# ---------------------------------------------------------------------------
# Canonical signal keys
# These are the field names the WattCast rule engine and execution log use.
# Drivers must map their brand-specific register names to these keys.
# ---------------------------------------------------------------------------

SIGNAL_KEYS = frozenset({
    # Battery
    "battery_soc",           # % (0–100)
    "battery_voltage",       # V
    "battery_current",       # A (positive = charging)
    "battery_power",         # W (positive = charging)
    "battery_temp",          # °C

    # PV (solar)
    "pv1_power",             # W
    "pv2_power",             # W
    "pv1_voltage",           # V
    "pv1_current",           # A
    "pv2_voltage",           # V
    "pv2_current",           # A

    # Grid
    "grid_voltage",          # V
    "grid_frequency",        # Hz
    "grid_power",            # W (positive = importing, negative = exporting)

    # Load
    "load_power",            # W
    "load_voltage",          # V

    # Inverter
    "inverter_temp",         # °C
    "work_mode",             # str — brand-specific label (e.g. "Zero Export to Load")

    # Daily energy totals (reset at midnight by the inverter)
    "day_pv_energy",         # kWh
    "day_battery_charge",    # kWh
    "day_battery_discharge", # kWh
    "day_grid_import",       # kWh
    "day_grid_export",       # kWh
    "day_load_energy",       # kWh
})

# Writable control signal keys — subset of SIGNAL_KEYS plus control-only keys.
# These are the keys that may appear in a Preset's settings blob.
WRITABLE_KEYS = frozenset({
    "work_mode",                      # int written to inverter (brand-specific mapping)
    "battery_min_soc",                # % — minimum SOC before inverter stops discharging
    "battery_max_charge_current",     # A
    "battery_max_discharge_current",  # A
    "tou_time_1",                     # HHMM int (e.g. 530 = 05:30)
    "tou_time_2",
    "tou_time_3",
    "tou_time_4",
    "tou_time_5",
    "tou_time_6",
    "tou_soc_1",                      # % target SOC for TOU slot
    "tou_soc_2",
    "tou_soc_3",
    "tou_soc_4",
    "tou_soc_5",
    "tou_soc_6",
})


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PresetResult:
    """Result of applying a preset. Captures partial failures."""
    success: bool
    applied: list[str] = field(default_factory=list)    # keys successfully written
    failed: list[tuple[str, str]] = field(default_factory=list)  # (key, error message)

    @property
    def partial(self) -> bool:
        return bool(self.applied) and bool(self.failed)

    def __str__(self) -> str:
        if self.success:
            return f"Preset applied ({len(self.applied)} settings written)"
        if self.partial:
            return (
                f"Preset partially applied: "
                f"{len(self.applied)} written, {len(self.failed)} failed "
                f"({', '.join(k for k, _ in self.failed)})"
            )
        return f"Preset failed: {'; '.join(f'{k}: {e}' for k, e in self.failed)}"


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class InverterDriver(ABC):
    """Abstract base for all LocalBus inverter drivers.

    Subclass this for each brand/connection method. Override all abstract
    methods. Call super().__init__() with the plant_id so logging is consistent.
    """

    def __init__(self, plant_id: str) -> None:
        self.plant_id = plant_id
        self.log = logging.getLogger(f"localbus.{self.__class__.__name__}.{plant_id}")

    # --- Connection lifecycle -------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Open the hardware connection. Idempotent — safe to call if already connected."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close the hardware connection cleanly."""
        ...

    def __enter__(self) -> "InverterDriver":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    # --- Core interface -------------------------------------------------

    @abstractmethod
    def read_snapshot(self) -> dict[str, Any]:
        """Read all live telemetry values and return them as a flat dict.

        Keys MUST be from SIGNAL_KEYS. Keys that the hardware does not
        support (e.g. pv2 on a single-string inverter) should be omitted
        rather than returning None — the rule engine will treat missing
        keys as unavailable signals and skip rules that reference them.

        Raises:
            ConnectionError: if the hardware cannot be reached.
            RuntimeError: if reads fail after retries.
        """
        ...

    @abstractmethod
    def apply_preset(self, settings: dict[str, Any], *, dry_run: bool = False) -> PresetResult:
        """Write a full preset settings blob to the inverter.

        `settings` is a dict of writable canonical key → value. All keys
        in the blob should be written. Unknown keys are logged and skipped.

        Args:
            settings: Canonical key → value mapping (from the Preset blob).
            dry_run:  If True, log what would happen but don't write.

        Returns:
            PresetResult describing which keys were written and which failed.
        """
        ...

    @abstractmethod
    def read_register(self, key: str) -> Any:
        """Read a single named register. Useful for diagnostics and verification.

        Args:
            key: Canonical signal or writable key name.

        Returns:
            The decoded value.

        Raises:
            KeyError: if the key is not supported by this driver.
            RuntimeError: if the read fails after retries.
        """
        ...

    @abstractmethod
    def ping(self) -> bool:
        """Quick liveness check. Returns True if the inverter is responding."""
        ...

    # --- Optional: brand metadata ---------------------------------------

    @property
    def brand(self) -> str:
        """Human-readable brand name, e.g. 'Sunsynk'."""
        return self.__class__.__name__

    @property
    def supported_writable_keys(self) -> frozenset[str]:
        """Which writable keys this driver supports. Default: all of WRITABLE_KEYS.
        Override if the hardware doesn't support all control registers."""
        return WRITABLE_KEYS
