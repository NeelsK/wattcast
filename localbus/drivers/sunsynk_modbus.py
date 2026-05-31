"""
localbus/drivers/sunsynk_modbus.py — Sunsynk RS485 (Modbus RTU) driver.

Implements the InverterDriver interface for Sunsynk hybrid inverters
connected over RS485 via a USB-RS485 adapter on the Pi.

Hardware: Sunsynk 5K-SG04LP1 (single-phase hybrid)
Protocol: Modbus RTU over RS485
Baud: 9600, 8N1, slave address 1 (default)

Wiring and Pi setup: see the Sunsynk Connect HARDWARE.md and SETUP.md
in /WattCast/research/Sunsynk Connect/.

Usage:
    from localbus.drivers.sunsynk_modbus import SunsynkModbusDriver

    driver = SunsynkModbusDriver(plant_id="eschatologist", port="/dev/sunsynk")
    with driver:
        snapshot = driver.read_snapshot()
        result = driver.apply_preset({"battery_min_soc": 20, "work_mode": 1})
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

from localbus.base import InverterDriver, PresetResult, WRITABLE_KEYS
from localbus.drivers.sunsynk_registers import (
    REGISTERS,
    Register,
    get as get_register,
    writable_registers,
)

log = logging.getLogger("localbus.sunsynk_modbus")

# Registers read on every snapshot() call — the canonical live telemetry set.
SNAPSHOT_KEYS = [
    "battery_soc", "battery_voltage", "battery_current", "battery_power", "battery_temp",
    "pv1_power", "pv1_voltage", "pv1_current",
    "pv2_power", "pv2_voltage", "pv2_current",
    "grid_voltage", "grid_frequency", "grid_power",
    "load_power", "load_voltage",
    "inverter_temp",
    "work_mode",
    "day_pv_energy", "day_battery_charge", "day_battery_discharge",
    "day_grid_import", "day_grid_export", "day_load_energy",
]


class SunsynkModbusDriver(InverterDriver):
    """Sunsynk RS485 Modbus RTU driver.

    Implements InverterDriver for the Sunsynk 5K-SG04LP1. Should also work
    for other Sunsynk single-phase hybrids that share the same register map
    (verify addresses against the LCD first).
    """

    brand = "Sunsynk"

    def __init__(
        self,
        plant_id: str,
        port: str = "/dev/sunsynk",
        baudrate: int = 9600,
        slave: int = 1,
        timeout: float = 2.0,
        retries: int = 3,
    ) -> None:
        super().__init__(plant_id)
        self.port = port
        self.baudrate = baudrate
        self.slave = slave
        self.timeout = timeout
        self.retries = retries
        self._client: ModbusSerialClient | None = None

    # --- Connection lifecycle ------------------------------------------

    def connect(self) -> None:
        """Open the RS485 connection. Idempotent."""
        if self._client and self._client.connected:
            return
        self._client = ModbusSerialClient(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self.timeout,
        )
        if not self._client.connect():
            raise ConnectionError(
                f"[{self.plant_id}] Could not open {self.port}. "
                f"Check the USB-RS485 adapter is connected and "
                f"the pi user is in the 'dialout' group."
            )
        self.log.info("Connected to %s @ %d baud (slave %d)", self.port, self.baudrate, self.slave)

    def disconnect(self) -> None:
        """Close the RS485 connection."""
        if self._client:
            self._client.close()
            self._client = None
            self.log.info("Disconnected from %s", self.port)

    # --- Core interface -----------------------------------------------

    def read_snapshot(self) -> dict[str, Any]:
        """Read all standard telemetry registers and return as a canonical dict.

        Keys are canonical signal names from SIGNAL_KEYS. If a register
        read fails, that key is omitted and the error is logged — the
        snapshot is partial rather than raising.
        """
        snapshot: dict[str, Any] = {}
        for key in SNAPSHOT_KEYS:
            try:
                snapshot[key] = self._read_one(get_register(key))
            except Exception as e:
                self.log.warning("Snapshot: failed to read '%s': %s", key, e)
                # Omit the key — rule engine treats missing keys as unavailable signals
        return snapshot

    def apply_preset(self, settings: dict[str, Any], *, dry_run: bool = False) -> PresetResult:
        """Write a full preset settings blob to the inverter.

        Iterates all keys in `settings`. Unknown or non-writable keys are
        logged and counted as failures. Each write is attempted individually
        so a single register failure doesn't abort the rest.
        """
        applied: list[str] = []
        failed: list[tuple[str, str]] = []

        for key, value in settings.items():
            try:
                reg = get_register(key)
            except KeyError as e:
                failed.append((key, str(e)))
                self.log.warning("apply_preset: unknown key '%s' — skipping", key)
                continue

            if not reg.writable:
                err = f"Register '{key}' is not writable"
                failed.append((key, err))
                self.log.warning("apply_preset: %s — skipping", err)
                continue

            if dry_run:
                raw = reg.encode(value)
                self.log.info(
                    "[DRY RUN] would write %s = %s (raw %d at address %d)",
                    key, value, raw, reg.address,
                )
                applied.append(key)
                continue

            try:
                self._write_one(reg, value)
                applied.append(key)
                self.log.info("Wrote %s = %s", key, value)
            except Exception as e:
                failed.append((key, str(e)))
                self.log.error("Failed to write %s = %s: %s", key, value, e)

        success = len(failed) == 0
        result = PresetResult(success=success, applied=applied, failed=failed)
        self.log.info("apply_preset: %s", result)
        return result

    def read_register(self, key: str) -> Any:
        """Read a single register by canonical key. Useful for verification."""
        reg = get_register(key)  # raises KeyError if unknown
        return self._read_one(reg)

    def ping(self) -> bool:
        """Check liveness by reading battery_soc and confirming it's 0–100."""
        try:
            soc = self.read_register("battery_soc")
            ok = 0 <= soc <= 100
            if not ok:
                self.log.warning("Ping: battery_soc out of range: %s", soc)
            return ok
        except Exception as e:
            self.log.warning("Ping failed: %s", e)
            return False

    # --- Internal Modbus helpers --------------------------------------

    def _read_one(self, reg: Register) -> Any:
        """Read a single register with retry logic."""
        last_err: Exception | None = None
        for attempt in range(self.retries):
            try:
                self.connect()
                assert self._client is not None
                rr = self._client.read_holding_registers(
                    address=reg.address,
                    count=reg.words,
                    slave=self.slave,
                )
                if rr.isError():
                    raise ModbusException(f"Modbus error on read of '{reg.canonical_key}': {rr}")
                return reg.decode(list(rr.registers))
            except (ModbusException, ConnectionError, OSError) as e:
                last_err = e
                self.log.warning(
                    "Read of '%s' failed (attempt %d/%d): %s",
                    reg.canonical_key, attempt + 1, self.retries, e,
                )
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(
            f"Failed to read '{reg.canonical_key}' after {self.retries} attempts"
        ) from last_err

    def _write_one(self, reg: Register, value: Any) -> None:
        """Write a single register. Caller is responsible for checking reg.writable."""
        raw = reg.encode(value)
        self.connect()
        assert self._client is not None
        rr = self._client.write_register(
            address=reg.address,
            value=raw,
            slave=self.slave,
        )
        if rr.isError():
            raise ModbusException(
                f"Modbus write error for '{reg.canonical_key}' (raw {raw}): {rr}"
            )
