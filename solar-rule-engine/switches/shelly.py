"""
switches/shelly.py — Local HTTP control for Shelly Wi-Fi switches.

Supports:
  Gen1 (Shelly 1, 1PM, 2.5, Plug, Plug S, etc.)
    GET http://<ip>/relay/0?turn=on|off
    GET http://<ip>/relay/0  → status

  Gen2 (Shelly Plus 1, Plus 1PM, Pro series, etc.)
    GET http://<ip>/rpc/Switch.Set?id=0&on=true|false
    GET http://<ip>/rpc/Switch.GetStatus?id=0  → status

Generation is detected automatically from the config, or can be forced.
Auth: Gen1 uses HTTP Basic (user:pass in URL). Gen2 uses digest auth.

No external libraries needed beyond requests.
"""

import logging
from typing import Optional

import requests
from requests.auth import HTTPDigestAuth

logger = logging.getLogger(__name__)

TIMEOUT = 5  # seconds


class ShellySwitch:
    """
    Controls a single Shelly relay channel over local HTTP.

    config dict shape (one entry from config.yaml switches list):
        name: "geyser"
        ip: "192.168.1.x"
        channel: 0          # relay channel index (0 for single-relay devices)
        gen: 1              # 1 or 2; omit to auto-detect
        username: ""        # optional, leave empty if no auth
        password: ""
    """

    def __init__(self, switch_config: dict, dry_run: bool = True):
        self.name = switch_config["name"]
        self._ip = switch_config["ip"]
        self._channel = switch_config.get("channel", 0)
        self._dry_run = dry_run
        self._username = switch_config.get("username", "")
        self._password = switch_config.get("password", "")

        # Determine generation
        gen = switch_config.get("gen")
        if gen is None:
            gen = self._detect_gen()
        self._gen = int(gen)

        logger.info(
            "Switch '%s' initialised: %s Gen%d channel %d%s",
            self.name, self._ip, self._gen, self._channel,
            " [DRY RUN]" if self._dry_run else "",
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def turn_on(self, reason: str = "") -> bool:
        return self._set(True, reason)

    def turn_off(self, reason: str = "") -> bool:
        return self._set(False, reason)

    def get_state(self) -> Optional[bool]:
        """
        Returns True if relay is on, False if off, None if unreachable.
        """
        try:
            if self._gen == 1:
                return self._gen1_get_state()
            return self._gen2_get_state()
        except Exception as e:
            logger.error("Switch '%s' get_state failed: %s", self.name, e)
            return None

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _set(self, on: bool, reason: str) -> bool:
        state_str = "on" if on else "off"
        if self._dry_run:
            logger.info(
                "[DRY RUN] Switch '%s' would turn %s  (%s)",
                self.name, state_str, reason or "no reason given",
            )
            return True

        try:
            if self._gen == 1:
                ok = self._gen1_set(on)
            else:
                ok = self._gen2_set(on)

            if ok:
                logger.info("Switch '%s' turned %s  (%s)", self.name, state_str, reason)
            else:
                logger.error("Switch '%s' turn %s FAILED", self.name, state_str)
            return ok

        except Exception as e:
            logger.error("Switch '%s' control error: %s", self.name, e)
            return False

    # --- Gen1 ---

    def _gen1_url(self, path: str) -> str:
        if self._username:
            return f"http://{self._username}:{self._password}@{self._ip}{path}"
        return f"http://{self._ip}{path}"

    def _gen1_set(self, on: bool) -> bool:
        state = "on" if on else "off"
        url = self._gen1_url(f"/relay/{self._channel}?turn={state}")
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("ison") == on

    def _gen1_get_state(self) -> Optional[bool]:
        url = self._gen1_url(f"/relay/{self._channel}")
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("ison"))

    # --- Gen2 ---

    def _gen2_auth(self):
        if self._username:
            return HTTPDigestAuth(self._username, self._password)
        return None

    def _gen2_set(self, on: bool) -> bool:
        url = f"http://{self._ip}/rpc/Switch.Set"
        params = {"id": self._channel, "on": "true" if on else "false"}
        resp = requests.get(url, params=params, auth=self._gen2_auth(), timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("was_on") is not None  # any valid response means success

    def _gen2_get_state(self) -> Optional[bool]:
        url = f"http://{self._ip}/rpc/Switch.GetStatus"
        params = {"id": self._channel}
        resp = requests.get(url, params=params, auth=self._gen2_auth(), timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("output"))

    # --- Auto-detect generation ---

    def _detect_gen(self) -> int:
        """
        Probe the device to determine Gen1 vs Gen2.
        Gen2 devices respond to /shelly with a 'gen' field.
        Fall back to Gen1 if that fails.
        """
        try:
            url = f"http://{self._ip}/shelly"
            resp = requests.get(url, timeout=3)
            data = resp.json()
            gen = int(data.get("gen", 1))
            logger.debug("Auto-detected Shelly Gen%d at %s", gen, self._ip)
            return gen
        except Exception:
            logger.debug("Gen detection failed for %s, assuming Gen1", self._ip)
            return 1
