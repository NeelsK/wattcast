#!/usr/bin/env python3
"""
WattCast Shelly Controller — Gen2 devices (Pro 1PM, Plus 1PM, etc.)

Gen2 Shelly uses the RPC API over HTTP:
  POST /rpc/Switch.Set   {"id": 0, "on": true/false}
  POST /rpc/Switch.GetStatus  {"id": 0}  → {"output": true/false, "apower": float, ...}

Auth: HTTP Digest (admin / password).

The existing Shelly script (shelly_relay_notifier.js) already filters out
HTTP source events to avoid logging WattCast's own control calls — so our
commands won't show up as "SHELLY" badge events in the weather site UI.
That's the correct behaviour.

WattCast does NOT replicate the webhook inbound path — it only sends outbound
RPC commands. Webhook inbound (state change notifications) are handled by
the existing weather_site infrastructure and are not needed for automation.

Usage:
  from shelly import ShellySwitch
  geyser = ShellySwitch("geyser", "10.69.69.43", password="...")
  geyser.turn_on()
  geyser.turn_off()
  state = geyser.get_state()   # True/False/None
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Optional
import urllib.parse

log = logging.getLogger("wattcast.shelly")


class ShellySwitch:
    """
    Controls a single relay channel on a Gen2 Shelly device.

    channel: relay channel index (0 for single-relay devices like Pro 1PM)
    timeout: HTTP request timeout in seconds
    """

    def __init__(
        self,
        name: str,
        host: str,
        password: str,
        username: str = "admin",
        channel: int = 0,
        timeout: int = 5,
    ):
        self.name     = name
        self.host     = host
        self.username = username
        self.password = password
        self.channel  = channel
        self.timeout  = timeout

        # Install digest auth handler globally for this instance's calls
        self._password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        self._password_mgr.add_password(None, f"http://{host}/", username, password)
        self._auth_handler = urllib.request.HTTPDigestAuthHandler(self._password_mgr)
        self._opener = urllib.request.build_opener(self._auth_handler)

    # ── Public API ────────────────────────────────────────────────────────────

    def turn_on(self) -> bool:
        """Turn relay ON. Returns True on success."""
        return self._set(True)

    def turn_off(self) -> bool:
        """Turn relay OFF. Returns True on success."""
        return self._set(False)

    def set_state(self, on: bool) -> bool:
        """Turn relay on (True) or off (False). Returns True on success."""
        return self._set(on)

    def get_state(self) -> Optional[bool]:
        """
        Read current relay state.
        Returns True (ON), False (OFF), or None on error.
        """
        result = self._rpc("Switch.GetStatus", {"id": self.channel})
        if result is None:
            return None
        output = result.get("output")
        if output is None:
            log.warning(f"Shelly '{self.name}': GetStatus response missing 'output': {result}")
            return None
        return bool(output)

    def get_power(self) -> Optional[float]:
        """
        Read current power consumption in Watts (Pro 1PM has energy monitoring).
        Returns float or None on error.
        """
        result = self._rpc("Switch.GetStatus", {"id": self.channel})
        if result is None:
            return None
        return result.get("apower")   # active power in W

    def status(self) -> Optional[dict]:
        """Full Switch.GetStatus response dict, or None on error."""
        return self._rpc("Switch.GetStatus", {"id": self.channel})

    # ── RPC internals ─────────────────────────────────────────────────────────

    def _set(self, on: bool) -> bool:
        """Send Switch.Set RPC. Returns True on success."""
        result = self._rpc("Switch.Set", {"id": self.channel, "on": on})
        if result is None:
            return False
        # Gen2 Switch.Set returns {"was_on": bool}
        log.info(f"Shelly '{self.name}': relay {'ON' if on else 'OFF'} "
                 f"(was_on={result.get('was_on')})")
        return True

    def _rpc(self, method: str, params: dict) -> Optional[dict]:
        """
        POST to /rpc/<method> with JSON params.
        Returns parsed response dict, or None on any error.

        Gen2 RPC format:
          POST /rpc/Switch.Set
          Content-Type: application/json
          Body: {"id": 0, "on": true}
        """
        url     = f"http://{self.host}/rpc/{method}"
        payload = json.dumps(params).encode("utf-8")

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)

        except urllib.error.HTTPError as e:
            log.warning(f"Shelly '{self.name}' RPC {method} HTTP error: {e.code} {e.reason}")
            return None
        except urllib.error.URLError as e:
            log.warning(f"Shelly '{self.name}' RPC {method} connection error: {e.reason}")
            return None
        except json.JSONDecodeError as e:
            log.warning(f"Shelly '{self.name}' RPC {method} JSON parse error: {e}")
            return None
        except Exception as e:
            log.warning(f"Shelly '{self.name}' RPC {method} unexpected error: {e}")
            return None


# ── Device registry ───────────────────────────────────────────────────────────

class ShellyRegistry:
    """
    Holds all configured Shelly devices for a site.
    Rule engine SwitchControl actions look up devices by name here.
    """

    def __init__(self):
        self._devices: dict[str, ShellySwitch] = {}

    def register(self, switch: ShellySwitch) -> None:
        self._devices[switch.name] = switch
        log.info(f"ShellyRegistry: registered '{switch.name}' at {switch.host}")

    def get(self, name: str) -> Optional[ShellySwitch]:
        device = self._devices.get(name)
        if device is None:
            log.warning(f"ShellyRegistry: no device named '{name}'")
        return device

    def names(self) -> list[str]:
        return list(self._devices.keys())


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    password = os.getenv("SHELLY_GEYSER_PASSWORD", "")
    if not password:
        print("Set SHELLY_GEYSER_PASSWORD env var to test")
        raise SystemExit(1)

    geyser = ShellySwitch(
        name="geyser",
        host="10.69.69.43",
        password=password,
    )

    print(f"\n=== Shelly '{geyser.name}' at {geyser.host} ===\n")

    state = geyser.get_state()
    power = geyser.get_power()
    print(f"Current state: {'ON' if state else 'OFF' if state is not None else 'UNKNOWN'}")
    print(f"Current power: {power}W")

    full = geyser.status()
    if full:
        print(f"Full status:   {full}")
