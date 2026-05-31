#!/usr/bin/env python3
"""
monitor.py — live terminal dashboard for the inverter.

Usage:
    python3 bin/monitor.py [--port /dev/ttyUSB0] [--interval 1.0]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make the package importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sunsynk import SunsynkClient


CLEAR = "\033[2J\033[H"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"


def colour_for_battery(soc: float) -> str:
    if soc > 60:
        return GREEN
    if soc > 30:
        return YELLOW
    return RED


def colour_for_grid(power: float) -> str:
    # Importing = bad (you're spending money), exporting = good
    if power > 0:
        return RED
    if power < -100:
        return GREEN
    return DIM


def render(s: dict) -> str:
    soc = s["battery_soc"]
    bat_pwr = s["battery_power"]
    pv = s["pv1_power"] + s["pv2_power"]
    grid = s["grid_power"]
    load = s["load_power"]

    bat_state = "charging" if bat_pwr > 50 else "discharging" if bat_pwr < -50 else "idle"
    bat_arrow = "↑" if bat_pwr > 50 else "↓" if bat_pwr < -50 else "·"

    grid_state = "importing" if grid > 0 else "exporting"

    lines = [
        f"{BOLD}{CYAN}─── Sunsynk Live ───{RESET}  {DIM}{time.strftime('%H:%M:%S')}{RESET}",
        "",
        f"  {BOLD}Battery{RESET}  {colour_for_battery(soc)}{soc:>5.0f}%{RESET}  "
        f"{bat_arrow} {abs(bat_pwr):>5.0f} W  ({bat_state})",
        f"  {BOLD}Solar  {RESET}  {YELLOW}{pv:>5.0f} W{RESET}  "
        f"({DIM}PV1 {s['pv1_power']:.0f} W · PV2 {s['pv2_power']:.0f} W{RESET})",
        f"  {BOLD}Grid   {RESET}  {colour_for_grid(grid)}{abs(grid):>5.0f} W{RESET}  "
        f"({grid_state} @ {s['grid_voltage']:.1f} V / {s['grid_frequency']:.2f} Hz)",
        f"  {BOLD}Load   {RESET}  {BLUE}{load:>5.0f} W{RESET}",
        "",
        f"  {DIM}Today: PV {s['day_pv_energy']:.1f} kWh · "
        f"Bat ↑{s['day_battery_charge']:.1f}/↓{s['day_battery_discharge']:.1f} kWh · "
        f"Grid ↑{s['day_grid_export']:.1f}/↓{s['day_grid_import']:.1f} kWh · "
        f"Load {s['day_load_energy']:.1f} kWh{RESET}",
        f"  {DIM}Inverter temp: {s['inverter_temp']:.1f}°C{RESET}",
        "",
        f"  {DIM}Ctrl-C to quit{RESET}",
    ]
    return CLEAR + "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Live Sunsynk inverter monitor")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--slave", type=int, default=1)
    p.add_argument("--interval", type=float, default=1.0,
                   help="Refresh interval in seconds (default 1.0)")
    args = p.parse_args()

    try:
        with SunsynkClient(port=args.port, baudrate=args.baud, slave=args.slave) as inv:
            while True:
                try:
                    snap = inv.snapshot()
                    print(render(snap), end="", flush=True)
                except Exception as e:
                    print(f"\n{RED}Read error:{RESET} {e}\n", file=sys.stderr)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except ConnectionError as e:
        print(f"{RED}{e}{RESET}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
