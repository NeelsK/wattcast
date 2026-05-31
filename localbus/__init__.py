"""
localbus/__init__.py — Driver registry and factory.

LocalBus is the WattCast hardware abstraction layer for inverter communication.
It provides a unified interface (InverterDriver) that the Pi rule engine uses
regardless of inverter brand or connection method.

Adding a new brand:
    1. Create localbus/drivers/<brand>_<method>.py implementing InverterDriver.
    2. Register it in DRIVER_REGISTRY below with a descriptive key.
    3. That's it — the rule engine and Pi agent need no changes.

Factory usage:
    from localbus import create_driver

    driver = create_driver(
        brand="sunsynk_modbus",
        plant_id="eschatologist",
        port="/dev/sunsynk",
    )
    with driver:
        snapshot = driver.read_snapshot()
"""

from __future__ import annotations

from typing import Any

from localbus.base import InverterDriver, PresetResult, SIGNAL_KEYS, WRITABLE_KEYS

# ---------------------------------------------------------------------------
# Driver registry
# Maps a brand/method key (used in plant config) to the driver class.
# Keys are lowercase, format: <brand>_<connection_method>
# ---------------------------------------------------------------------------

# Lazy imports — drivers are only imported if actually used, so a Pi without
# pymodbus installed won't fail to import localbus just because the Sunsynk
# driver is registered here.

def _load_sunsynk_modbus():
    from localbus.drivers.sunsynk_modbus import SunsynkModbusDriver
    return SunsynkModbusDriver


DRIVER_REGISTRY: dict[str, callable] = {
    "sunsynk_modbus": _load_sunsynk_modbus,
    # Future drivers — add entries here:
    # "deye_modbus":    _load_deye_modbus,
    # "victron_vebus":  _load_victron_vebus,
    # "goodwe_modbus":  _load_goodwe_modbus,
}


def create_driver(brand: str, plant_id: str, **kwargs: Any) -> InverterDriver:
    """Instantiate the correct driver for a plant's inverter brand.

    Args:
        brand:    Driver key from DRIVER_REGISTRY (e.g. "sunsynk_modbus").
                  Matches the `inverter.local_driver` field in the plant config.
        plant_id: Unique plant identifier — used in log messages.
        **kwargs: Driver-specific connection parameters (port, baudrate, etc.).
                  These come from the plant's inverter config block.

    Returns:
        A connected-ready InverterDriver instance (not yet connected — call
        connect() or use as a context manager).

    Raises:
        ValueError: if the brand key is not in DRIVER_REGISTRY.
    """
    if brand not in DRIVER_REGISTRY:
        available = ", ".join(sorted(DRIVER_REGISTRY))
        raise ValueError(
            f"Unknown inverter driver '{brand}'. "
            f"Available drivers: {available}. "
            f"To add a new brand, see localbus/__init__.py."
        )
    driver_class = DRIVER_REGISTRY[brand]()
    return driver_class(plant_id=plant_id, **kwargs)


__all__ = [
    "InverterDriver",
    "PresetResult",
    "SIGNAL_KEYS",
    "WRITABLE_KEYS",
    "DRIVER_REGISTRY",
    "create_driver",
]
