"""
switches/manager.py — Loads all configured switches and provides a
unified interface for the engine to query and control them.

Supports:
  type: shelly    — ShellySwitch (local HTTP, Gen1/Gen2)
  type: http      — GenericHTTPSwitch (arbitrary on/off URLs — for Tasmota,
                    TP-Link, or any REST-controllable switch)

Adding a new switch type: implement a class with turn_on(), turn_off(),
get_state() → Optional[bool], and register it in SWITCH_DRIVERS below.
"""

import logging
from typing import Optional, Protocol

from switches.shelly import ShellySwitch

logger = logging.getLogger(__name__)


class SwitchDriver(Protocol):
    """Structural protocol — any class with these methods qualifies."""
    name: str
    def turn_on(self, reason: str = "") -> bool: ...
    def turn_off(self, reason: str = "") -> bool: ...
    def get_state(self) -> Optional[bool]: ...


class GenericHTTPSwitch:
    """
    Fallback driver for any switch with separate on/off HTTP endpoints.
    Tasmota, TP-Link Kasa (local mode), or anything REST-accessible.

    config shape:
        name: "pool_pump"
        type: "http"
        url_on:  "http://192.168.1.x/cm?cmnd=Power%20On"   # Tasmota example
        url_off: "http://192.168.1.x/cm?cmnd=Power%20Off"
        url_status: "http://192.168.1.x/cm?cmnd=Power"     # optional
        status_on_value: "ON"   # string to look for in response to indicate ON
    """

    def __init__(self, switch_config: dict, dry_run: bool = True):
        self.name = switch_config["name"]
        self._url_on = switch_config["url_on"]
        self._url_off = switch_config["url_off"]
        self._url_status = switch_config.get("url_status")
        self._status_on_value = switch_config.get("status_on_value", "ON")
        self._dry_run = dry_run

    def turn_on(self, reason: str = "") -> bool:
        if self._dry_run:
            logger.info("[DRY RUN] Switch '%s' would turn on  (%s)", self.name, reason)
            return True
        import requests
        try:
            requests.get(self._url_on, timeout=5).raise_for_status()
            logger.info("Switch '%s' turned on  (%s)", self.name, reason)
            return True
        except Exception as e:
            logger.error("Switch '%s' turn_on failed: %s", self.name, e)
            return False

    def turn_off(self, reason: str = "") -> bool:
        if self._dry_run:
            logger.info("[DRY RUN] Switch '%s' would turn off  (%s)", self.name, reason)
            return True
        import requests
        try:
            requests.get(self._url_off, timeout=5).raise_for_status()
            logger.info("Switch '%s' turned off  (%s)", self.name, reason)
            return True
        except Exception as e:
            logger.error("Switch '%s' turn_off failed: %s", self.name, e)
            return False

    def get_state(self) -> Optional[bool]:
        if not self._url_status:
            return None
        import requests
        try:
            resp = requests.get(self._url_status, timeout=5)
            return self._status_on_value in resp.text
        except Exception:
            return None


SWITCH_DRIVERS = {
    "shelly": ShellySwitch,
    "http": GenericHTTPSwitch,
}


class SwitchManager:
    """
    Instantiates all switches from config and provides a name-keyed registry.

    Usage:
        manager = SwitchManager(config)
        manager["geyser"].turn_on(reason="surplus solar")
        state = manager["geyser"].get_state()
    """

    def __init__(self, config: dict):
        dry_run = config["engine"].get("dry_run", True)
        self._switches: dict[str, SwitchDriver] = {}

        for sw_cfg in config.get("switches", []):
            sw_type = sw_cfg.get("type", "shelly")
            driver_cls = SWITCH_DRIVERS.get(sw_type)
            if driver_cls is None:
                logger.warning("Unknown switch type '%s' for '%s' — skipping", sw_type, sw_cfg.get("name"))
                continue
            driver = driver_cls(sw_cfg, dry_run=dry_run)
            self._switches[driver.name] = driver

        logger.info("SwitchManager: loaded %d switch(es): %s", len(self._switches), list(self._switches))

    def __getitem__(self, name: str) -> SwitchDriver:
        return self._switches[name]

    def __contains__(self, name: str) -> bool:
        return name in self._switches

    def names(self) -> list[str]:
        return list(self._switches.keys())

    def get_all_states(self) -> dict[str, Optional[bool]]:
        """Poll all switches for current state. Used for logging/diagnostics."""
        return {name: sw.get_state() for name, sw in self._switches.items()}
