#!/usr/bin/env python3
"""
logger.py — write a snapshot to SQLite once per interval. Run as a
systemd service for continuous logging.

Usage:
    python3 bin/logger.py [--db ~/sunsynk.db] [--interval 60]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sunsynk import SunsynkClient

log = logging.getLogger("sunsynk.logger")

# Schema kept simple: one wide row per sample. Easy to query, easy to grow.
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts                       INTEGER PRIMARY KEY,    -- unix timestamp (seconds)
    battery_soc              REAL,
    battery_voltage          REAL,
    battery_current          REAL,
    battery_power            REAL,
    pv1_power                REAL,
    pv2_power                REAL,
    grid_voltage             REAL,
    grid_frequency           REAL,
    grid_power               REAL,
    load_power               REAL,
    inverter_temp            REAL,
    day_pv_energy            REAL,
    day_battery_charge       REAL,
    day_battery_discharge    REAL,
    day_grid_import          REAL,
    day_grid_export          REAL,
    day_load_energy          REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
"""

COLUMNS = [
    "battery_soc", "battery_voltage", "battery_current", "battery_power",
    "pv1_power", "pv2_power",
    "grid_voltage", "grid_frequency", "grid_power",
    "load_power",
    "inverter_temp",
    "day_pv_energy", "day_battery_charge", "day_battery_discharge",
    "day_grid_import", "day_grid_export", "day_load_energy",
]


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_sample(conn: sqlite3.Connection, ts: int, snap: dict) -> None:
    placeholders = ", ".join(["?"] * (len(COLUMNS) + 1))
    cols = ", ".join(["ts"] + COLUMNS)
    values = [ts] + [snap.get(c) for c in COLUMNS]
    conn.execute(
        f"INSERT OR REPLACE INTO samples ({cols}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description="Continuous Sunsynk → SQLite logger")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--slave", type=int, default=1)
    p.add_argument("--db", type=Path, default=Path.home() / "sunsynk.db")
    p.add_argument("--interval", type=int, default=60,
                   help="Seconds between samples (default 60)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    log.info("Logging to %s every %ds", args.db, args.interval)
    conn = init_db(args.db)

    # Graceful shutdown
    stop = {"now": False}
    def handler(signum, frame):
        log.info("Received signal %d, stopping...", signum)
        stop["now"] = True
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    inv = SunsynkClient(port=args.port, baudrate=args.baud, slave=args.slave)
    consecutive_errors = 0

    while not stop["now"]:
        start = time.time()
        try:
            snap = inv.snapshot()
            insert_sample(conn, int(start), snap)
            log.debug("Sample @ %d: SOC=%s PV=%sW Load=%sW",
                     int(start), snap["battery_soc"],
                     snap["pv1_power"] + snap["pv2_power"], snap["load_power"])
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log.error("Sample failed (%d in a row): %s", consecutive_errors, e)
            if consecutive_errors >= 10:
                log.error("Too many consecutive errors, reconnecting...")
                inv.close()
                consecutive_errors = 0

        # Sleep the remainder of the interval, breakable by SIGINT
        elapsed = time.time() - start
        wait = max(0, args.interval - elapsed)
        for _ in range(int(wait * 10)):
            if stop["now"]:
                break
            time.sleep(0.1)

    inv.close()
    conn.close()
    log.info("Logger stopped cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
