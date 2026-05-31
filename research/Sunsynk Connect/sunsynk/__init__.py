"""Sunsynk Connect — local Modbus client for Sunsynk hybrid inverters."""

from .client import SunsynkClient
from .registers import REGISTERS, Register

__all__ = ["SunsynkClient", "REGISTERS", "Register"]
__version__ = "0.1.0"
