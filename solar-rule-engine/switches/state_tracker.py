"""
switches/state_tracker.py — Tracks run-time state for switches.

Answers questions the engine needs:
  - Is this switch currently on?
  - How long has it been on this session?
  - How many minutes has it run today?
  - Has it met its minimum run time (don't short-cycle)?
  - Has it exhausted its daily budget?

State is in-memory only. A restart resets it — that's acceptable since
the engine re-evaluates from live data on startup. For geyser control
the worst case of a restart is one extra heating cycle, which is safe.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SwitchRunState:
    """Runtime state for a single switch."""
    name: str

    # Current session
    is_on: bool = False
    turned_on_at: Optional[datetime] = None

    # Daily accounting
    today: date = field(default_factory=date.today)
    minutes_run_today: float = 0.0

    # Config (set from switch config)
    min_on_minutes: float = 10.0       # Minimum run before allow turn-off
    max_daily_minutes: float = 180.0   # Daily budget (3h default)
    min_off_minutes: float = 5.0       # Minimum off-time before allow turn-on again
    turned_off_at: Optional[datetime] = None

    def _check_day_rollover(self):
        today = date.today()
        if today != self.today:
            logger.debug("Switch '%s': day rollover, resetting daily counter", self.name)
            self.today = today
            self.minutes_run_today = 0.0

    @property
    def session_minutes(self) -> float:
        """Minutes on in the current session (0 if off)."""
        if not self.is_on or self.turned_on_at is None:
            return 0.0
        return (datetime.now() - self.turned_on_at).total_seconds() / 60

    @property
    def minutes_off(self) -> float:
        """Minutes since last turned off (large number if never turned off)."""
        if self.turned_off_at is None:
            return 9999.0
        return (datetime.now() - self.turned_off_at).total_seconds() / 60

    def record_turn_on(self):
        self._check_day_rollover()
        self.is_on = True
        self.turned_on_at = datetime.now()

    def record_turn_off(self):
        self._check_day_rollover()
        if self.is_on and self.turned_on_at:
            session = (datetime.now() - self.turned_on_at).total_seconds() / 60
            self.minutes_run_today += session
            logger.debug(
                "Switch '%s': session=%.1f min, today total=%.1f min",
                self.name, session, self.minutes_run_today,
            )
        self.is_on = False
        self.turned_off_at = datetime.now()
        self.turned_on_at = None

    # --- Guard checks (engine calls these before acting) ---

    def can_turn_on(self) -> tuple[bool, str]:
        """Returns (allowed, reason_if_blocked)."""
        self._check_day_rollover()

        if self.is_on:
            return False, "already on"

        remaining_budget = self.max_daily_minutes - self.minutes_run_today
        if remaining_budget <= 0:
            return False, f"daily budget exhausted ({self.minutes_run_today:.0f}/{self.max_daily_minutes:.0f} min)"

        if self.minutes_off < self.min_off_minutes:
            return False, f"minimum off-time not met ({self.minutes_off:.1f}/{self.min_off_minutes:.0f} min)"

        return True, ""

    def can_turn_off(self) -> tuple[bool, str]:
        """Returns (allowed, reason_if_blocked)."""
        if not self.is_on:
            return False, "already off"

        if self.session_minutes < self.min_on_minutes:
            remaining = self.min_on_minutes - self.session_minutes
            return False, f"minimum on-time not met ({self.session_minutes:.1f}/{self.min_on_minutes:.0f} min, {remaining:.1f} min remaining)"

        return True, ""

    def status_str(self) -> str:
        self._check_day_rollover()
        if self.is_on:
            return f"ON ({self.session_minutes:.1f} min session, {self.minutes_run_today:.1f}/{self.max_daily_minutes:.0f} min today)"
        return f"OFF ({self.minutes_run_today:.1f}/{self.max_daily_minutes:.0f} min today, off for {self.minutes_off:.1f} min)"


class SwitchStateRegistry:
    """Holds SwitchRunState for all configured switches."""

    def __init__(self, config: dict):
        self._states: dict[str, SwitchRunState] = {}
        for sw_cfg in config.get("switches", []):
            name = sw_cfg["name"]
            self._states[name] = SwitchRunState(
                name=name,
                min_on_minutes=sw_cfg.get("min_on_minutes", 10.0),
                max_daily_minutes=sw_cfg.get("max_daily_minutes", 180.0),
                min_off_minutes=sw_cfg.get("min_off_minutes", 5.0),
            )

    def __getitem__(self, name: str) -> SwitchRunState:
        return self._states[name]

    def __contains__(self, name: str) -> bool:
        return name in self._states

    def log_all(self):
        for state in self._states.values():
            logger.info("  Switch '%s': %s", state.name, state.status_str())
