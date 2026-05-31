#!/usr/bin/env python3
"""
automate.py — simple rule engine.

Loads rules from a YAML file. Every `interval` seconds, reads the
inverter snapshot, evaluates each rule's `when` expression against it,
and runs the rule's `do` action if the expression is true. Each rule
fires at most once per cooldown window.

Run with:
    python3 bin/automate.py --rules automation/rules.example.yaml

The rule expression sandbox is intentionally limited — it sees a dict
called `s` (snapshot values), plus `now()` returning the local time.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import signal
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sunsynk import SunsynkClient
from sunsynk.registers import REGISTERS, WORK_MODES

log = logging.getLogger("sunsynk.automate")


SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "round": round,
    "int": int, "float": float, "len": len,
    "True": True, "False": False,
}


def evaluate(expr: str, snapshot: dict) -> bool:
    """Evaluate a rule expression against a snapshot. The snapshot is
    accessible as `s.<key>` or `s["<key>"]`."""
    class Bag(dict):
        __getattr__ = dict.__getitem__

    ctx = {
        "s": Bag(snapshot),
        "now": lambda: dt.datetime.now(),
        **SAFE_BUILTINS,
    }
    return bool(eval(expr, {"__builtins__": {}}, ctx))


def perform(action: dict, inv: SunsynkClient, dry_run: bool) -> None:
    """Run a single rule action."""
    kind = action.get("type")
    if kind == "log":
        log.info("RULE-LOG: %s", action.get("message", "(no message)"))
        return
    if kind == "set":
        name = action["register"]
        value = int(action["value"])
        log.info("RULE-SET: %s = %s%s", name, value, " (dry run)" if dry_run else "")
        inv.write(name, value, dry_run=dry_run)
        return
    if kind == "shell":
        import subprocess
        cmd = action["command"]
        log.info("RULE-SHELL: %s", cmd)
        if not dry_run:
            subprocess.run(cmd, shell=True, check=False)
        return
    raise ValueError(f"Unknown action type: {kind}")


def load_rules(path: Path) -> list[dict]:
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    rules = raw.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("'rules' must be a list in the YAML file")
    # Validate
    for i, r in enumerate(rules):
        for required in ("name", "when", "do"):
            if required not in r:
                raise ValueError(f"Rule #{i} missing '{required}'")
    return rules


def main() -> int:
    p = argparse.ArgumentParser(description="Sunsynk rule engine")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--slave", type=int, default=1)
    p.add_argument("--rules", type=Path, required=True)
    p.add_argument("--interval", type=int, default=30,
                   help="Seconds between rule evaluations")
    p.add_argument("--dry-run", action="store_true",
                   help="Log what would happen but never write")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    rules = load_rules(args.rules)
    log.info("Loaded %d rules from %s%s", len(rules), args.rules,
             " (DRY RUN)" if args.dry_run else "")

    last_fired: dict[str, float] = {}

    stop = {"now": False}
    def handler(s, f):
        stop["now"] = True
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    inv = SunsynkClient(port=args.port, baudrate=args.baud, slave=args.slave)

    while not stop["now"]:
        try:
            snap = inv.snapshot()
        except Exception as e:
            log.error("Snapshot failed: %s", e)
            time.sleep(args.interval)
            continue

        now = time.time()
        for rule in rules:
            cooldown = rule.get("cooldown", 300)  # seconds
            last = last_fired.get(rule["name"], 0)
            if now - last < cooldown:
                continue
            try:
                if evaluate(rule["when"], snap):
                    log.info("Rule fired: %s", rule["name"])
                    actions = rule["do"]
                    if isinstance(actions, dict):
                        actions = [actions]
                    for action in actions:
                        perform(action, inv, dry_run=args.dry_run)
                    last_fired[rule["name"]] = now
            except Exception as e:
                log.error("Rule '%s' failed: %s", rule["name"], e)

        # sleep, but breakable
        for _ in range(args.interval * 10):
            if stop["now"]:
                break
            time.sleep(0.1)

    inv.close()
    log.info("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
