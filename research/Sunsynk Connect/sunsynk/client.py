"""
SunsynkClient — a thin, friendly wrapper around pymodbus for talking
to a Sunsynk hybrid inverter over RS485.

Usage:
    with SunsynkClient("/dev/ttyUSB0") as inv:
        soc = inv.read("battery_soc")
        print(f"Battery: {soc}%")
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

from .registers import REGISTERS, Register, get as get_register

log = logging.getLogger("sunsynk.client")


class SunsynkClient:
    """Synchronous Modbus RTU client for Sunsynk inverters.

    Designed for low-frequency polling (1 Hz). Not thread-safe — wrap calls
    in a lock if multiple coroutines/threads will share an instance.
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        slave: int = 1,
        timeout: float = 2.0,
        retries: int = 3,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.slave = slave
        self.timeout = timeout
        self.retries = retries
        self._client: ModbusSerialClient | None = None

    # --- Connection lifecycle ---------------------------------------

    def connect(self) -> None:
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
                f"Could not open serial port {self.port}. "
                f"Check the USB-RS485 adapter is plugged in and you have "
                f"permission to access it (try `sudo usermod -aG dialout $USER`)."
            )
        log.info("Connected to %s @ %d baud (slave %d)", self.port, self.baudrate, self.slave)

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SunsynkClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- Reads -------------------------------------------------------

    def read(self, name: str) -> Any:
        """Read a single named register and return its decoded value."""
        reg = get_register(name)
        return self._read_register(reg)

    def read_many(self, names: list[str]) -> dict[str, Any]:
        """Read several registers. Currently one-by-one; could be optimised
        to use bulk reads when registers are contiguous."""
        return {name: self.read(name) for name in names}

    def snapshot(self) -> dict[str, Any]:
        """Read the standard 'live values' set — what you'd show on a dashboard."""
        names = [
            "battery_soc", "battery_voltage", "battery_current", "battery_power",
            "pv1_power", "pv2_power",
            "grid_voltage", "grid_frequency", "grid_power",
            "load_power",
            "inverter_temp",
            "day_pv_energy", "day_battery_charge", "day_battery_discharge",
            "day_grid_import", "day_grid_export", "day_load_energy",
        ]
        return self.read_many(names)

    def _read_register(self, reg: Register) -> Any:
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
                    raise ModbusException(f"Modbus error reading {reg.name}: {rr}")
                return reg.decode(list(rr.registers))
            except (ModbusException, ConnectionError, OSError) as e:
                last_err = e
                log.warning("Read of %s failed (attempt %d/%d): %s",
                            reg.name, attempt + 1, self.retries, e)
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Failed to read {reg.name} after {self.retries} attempts") from last_err

    # --- Writes ------------------------------------------------------

    def write(self, name: str, value: int, *, dry_run: bool = False) -> None:
        """Write a value to a named register.

        Set dry_run=True to log the write that would have happened without
        actually doing it. Strongly recommended for the first run of any
        new control script.
        """
        reg = get_register(name)
        if not reg.writable:
            raise ValueError(
                f"Register '{name}' is not marked writable. "
                f"If you're sure it should be, edit registers.py — "
                f"but read the current value first and confirm against the LCD."
            )
        # Unscale if needed
        raw = int(round(value / reg.scale)) if reg.scale != 1.0 else int(value)
        if dry_run:
            log.info("[DRY RUN] would write %s = %s (raw %d) at address %d",
                     reg.name, value, raw, reg.address)
            return
        self.connect()
        assert self._client is not None
        rr = self._client.write_register(
            address=reg.address, value=raw, slave=self.slave,
        )
        if rr.isError():
            raise ModbusException(f"Modbus write error for {reg.name}: {rr}")
        log.info("Wrote %s = %s (raw %d)", reg.name, value, raw)

    # --- Diagnostics -------------------------------------------------

    def ping(self) -> bool:
        """Quick liveness check: read battery_soc and return True if it
        looks plausible."""
        try:
            soc = self.read("battery_soc")
            return 0 <= soc <= 100
        except Exception as e:
            log.warning("Ping failed: %s", e)
            return False


@contextmanager
def open_inverter(**kwargs):
    """Convenience context manager: `with open_inverter() as inv: ...`"""
    client = SunsynkClient(**kwargs)
    try:
        client.connect()
        yield client
    finally:
        client.close()
