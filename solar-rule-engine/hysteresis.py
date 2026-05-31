"""
hysteresis.py — Prevent inverter settings from flip-flopping.

Rules:
  1. Only send a command if the value actually changed.
  2. Only send a command if enough time has passed since the last change
     for that topic (configurable, default 15 minutes).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from models import Command

logger = logging.getLogger(__name__)


@dataclass
class HysteresisTracker:
    min_interval_minutes: int = 15

    _last_value: dict[str, str] = field(default_factory=dict)
    _last_change: dict[str, datetime] = field(default_factory=dict)

    def filter(self, commands: list[Command]) -> list[Command]:
        """
        Return only commands that pass the hysteresis check.
        Updates internal state for commands that pass.
        """
        approved: list[Command] = []
        now = datetime.now()

        for cmd in commands:
            key = cmd.topic_suffix
            last_val = self._last_value.get(key)
            last_time = self._last_change.get(key, datetime.min)
            elapsed = now - last_time

            if cmd.value == last_val:
                logger.debug("HYSTERESIS skip (no change): %s = %r", key, cmd.value)
                continue

            min_delta = timedelta(minutes=self.min_interval_minutes)
            if elapsed < min_delta:
                remaining = int((min_delta - elapsed).total_seconds() / 60)
                logger.debug(
                    "HYSTERESIS skip (too soon, %d min remaining): %s = %r",
                    remaining, key, cmd.value
                )
                continue

            # Passed — approve and record
            self._last_value[key] = cmd.value
            self._last_change[key] = now
            approved.append(cmd)

        return approved

    def current_state(self) -> dict[str, str]:
        """Return last known commanded values — useful for logging."""
        return dict(self._last_value)
