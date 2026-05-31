#!/usr/bin/env python3
"""
control.py — change inverter settings from the CLI.

Every write defaults to --dry-run unless you pass --commit. This is
deliberate: a wrong write can change real inverter behaviour
(e.g. drain your battery into the grid).

Examples:
    # Read current values (always safe)
    python3 bin/control.py read work_mode
    python3 bin/control.py read battery_min_soc

    # Dry run — see what would happen
    python3 bin/control.py set work_mode 1
    python3 bin/control.py set battery_min_soc 25

    # Actually apply
    python3 bin/control.py set battery_min_soc 25 --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sunsynk import SunsynkClient
from sunsynk.registers import REGISTERS, WORK_MODES

log = logging.getLogger("sunsynk.control")


def cmd_list(args, inv):
    """List all known registers."""
    print(f"{'NAME':<32} {'ADDR':>5} {'UNIT':<6} {'WRITABLE':<8} DESCRIPTION")
    for name, reg in sorted(REGISTERS.items()):
        print(f"{name:<32} {reg.address:>5} {reg.unit:<6} "
              f"{'yes' if reg.writable else 'no':<8} {reg.description}")
    return 0


def cmd_read(args, inv):
    """Read one or more registers."""
    for name in args.names:
        try:
            value = inv.read(name)
            unit = REGISTERS[name].unit
            print(f"{name:<32} = {value} {unit}".rstrip())
        except Exception as e:
            print(f"{name}: ERROR {e}", file=sys.stderr)
    return 0


def cmd_set(args, inv):
    """Write a register. Default is dry-run; pass --commit to apply."""
    name = args.name
    value = args.value
    if name not in REGISTERS:
        print(f"Unknown register '{name}'. Try `control.py list`.", file=sys.stderr)
        return 2
    reg = REGISTERS[name]
    if not reg.writable:
        print(f"Register '{name}' is not writable in this map.", file=sys.stderr)
        return 2

    # Show current value first — so a typo'd address screams loudly
    try:
        current = inv.read(name)
        print(f"Current {name} = {current} {reg.unit}")
    except Exception as e:
        print(f"Could not read current value: {e}", file=sys.stderr)
        if not args.force:
            print("Aborting. Pass --force to write anyway (not recommended).", file=sys.stderr)
            return 1

    # Prepare the write
    new_value = int(value)
    if name == "work_mode":
        if new_value not in WORK_MODES:
            print(f"work_mode must be one of {list(WORK_MODES.keys())}: {WORK_MODES}",
                  file=sys.stderr)
            return 2
        print(f"  → would set to {new_value} ({WORK_MODES[new_value]})")
    else:
        print(f"  → would set to {new_value} {reg.unit}")

    if not args.commit:
        print("DRY RUN. Pass --commit to actually write.")
        inv.write(name, new_value, dry_run=True)
        return 0

    # Confirm before committing real changes
    if not args.yes:
        resp = input(f"Type the register name '{name}' to confirm: ").strip()
        if resp != name:
            print("Aborted.")
            return 1

    inv.write(name, new_value, dry_run=False)
    # Read it back as confirmation
    try:
        readback = inv.read(name)
        print(f"Wrote OK. Readback: {name} = {readback} {reg.unit}")
    except Exception as e:
        print(f"Wrote, but readback failed: {e}", file=sys.stderr)
    return 0


def cmd_status(args, inv):
    """One-shot snapshot."""
    snap = inv.snapshot()
    for k, v in snap.items():
        unit = REGISTERS[k].unit
        print(f"{k:<32} = {v} {unit}".rstrip())
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Sunsynk control CLI")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--slave", type=int, default=1)

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List all known registers")
    sub.add_parser("status", help="Print a live snapshot")

    pr = sub.add_parser("read", help="Read named register(s)")
    pr.add_argument("names", nargs="+", help="Register name(s)")

    ps = sub.add_parser("set", help="Set a writable register")
    ps.add_argument("name", help="Register name")
    ps.add_argument("value", type=int, help="Integer value (in display units)")
    ps.add_argument("--commit", action="store_true",
                    help="Actually perform the write (default is dry run)")
    ps.add_argument("--yes", action="store_true",
                    help="Skip the typed confirmation prompt")
    ps.add_argument("--force", action="store_true",
                    help="Write even if the read-back of the current value fails")

    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # `list` doesn't need a serial connection — it just prints the static map.
    if args.cmd == "list":
        return cmd_list(args, inv=None)

    handlers = {
        "read":   cmd_read,
        "set":    cmd_set,
        "status": cmd_status,
    }
    handler = handlers[args.cmd]

    try:
        with SunsynkClient(port=args.port, baudrate=args.baud, slave=args.slave) as inv:
            return handler(args, inv)
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
