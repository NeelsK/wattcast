#!/usr/bin/env python3
"""
tools/forecast_bias.py — Open-Meteo forecast bias analysis.

Compares Open-Meteo's historical GTI-based yield model against actual
generation recorded in the WattCast MariaDB, for the past N days.

Usage (run from solar-rule-engine/ directory):
    python tools/forecast_bias.py --config config.yaml --days 30

Output:
    - Per-day table: modelled kWh vs actual kWh vs ratio
    - Overall bias multiplier to apply to forecasts
    - Suggested EFFICIENCY value for openmeteo.py

How it works:
    1. Pulls daily MAX(generation_today) from weather_solar_readings — this
       is the actual kWh generated each day as reported by the inverter.
    2. Fetches Open-Meteo ARCHIVE API (historical actuals, not forecast) for
       the same date range — uses global_tilted_irradiance.
    3. Applies the same yield model as openmeteo.py (GTI/1000 * kWp * efficiency)
       with efficiency=0.80 as baseline.
    4. Computes ratio = actual / modelled for each day, derives mean + stdev.
    5. Recommends a calibrated efficiency = 0.80 * mean_ratio.

Note: Open-Meteo archive uses ERA5 reanalysis data — it's the actual observed
irradiance, not the forecast. So this measures model accuracy, not forecast error.
The forecast uses the same GTI calculation, so the bias should transfer.
"""

import argparse
import sys
import os
from datetime import date, timedelta
from statistics import mean, stdev

import requests
import pymysql
import pymysql.cursors
import yaml
import zoneinfo

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
EFFICIENCY_BASELINE = 0.80


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# DB: pull actual daily generation
# ---------------------------------------------------------------------------

def fetch_actual_generation(config: dict, start: date, end: date) -> dict[date, float]:
    """
    Returns {date: actual_kwh} from weather_solar_readings.
    Uses MAX(generation_today) per day — the last reading of the day
    has the cumulative total.
    """
    db = config["mariadb"]
    conn = pymysql.connect(
        host=db["host"], port=db.get("port", 3306),
        user=db["user"], password=db["password"], database=db["database"],
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DATE(timestamp) AS day, MAX(generation_today) AS kwh
                FROM weather_solar_readings
                WHERE DATE(timestamp) BETWEEN %s AND %s
                  AND generation_today IS NOT NULL
                  AND generation_today > 0
                GROUP BY DATE(timestamp)
                ORDER BY day
            """, (start.isoformat(), end.isoformat()))
            rows = cur.fetchall()
    finally:
        conn.close()

    return {row["day"]: float(row["kwh"]) for row in rows}


# ---------------------------------------------------------------------------
# Open-Meteo archive: pull historical GTI
# ---------------------------------------------------------------------------

def fetch_archive_gti(config: dict, start: date, end: date) -> dict[date, float]:
    """
    Returns {date: modelled_kwh} using Open-Meteo archive GTI + our yield model.
    """
    loc    = config["location"]
    panels = config["panels"]
    tz     = zoneinfo.ZoneInfo(loc["timezone"])

    params = {
        "latitude":  loc["latitude"],
        "longitude": loc["longitude"],
        "timezone":  loc["timezone"],
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "hourly": "global_tilted_irradiance",
        "tilt":    panels["tilt"],
        "azimuth": panels["azimuth"],
    }

    print(f"  Fetching Open-Meteo archive {start} → {end}...")
    resp = requests.get(ARCHIVE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    hourly = data["hourly"]
    kwp = panels["total_kwp"]

    # Accumulate kWh per calendar day
    daily: dict[date, float] = {}
    for ts_str, gti in zip(hourly["time"], hourly["global_tilted_irradiance"]):
        if gti is None:
            continue
        dt = date.fromisoformat(ts_str[:10])
        kwh = (gti / 1000) * kwp * EFFICIENCY_BASELINE
        daily[dt] = daily.get(dt, 0.0) + kwh

    return daily


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse(actual: dict[date, float], modelled: dict[date, float]) -> None:
    common_days = sorted(set(actual) & set(modelled))

    if not common_days:
        print("\nNo overlapping days between DB and archive data.")
        sys.exit(1)

    print(f"\n{'Date':<12} {'Actual (kWh)':>14} {'Modelled (kWh)':>16} {'Ratio':>8}  Note")
    print("-" * 65)

    ratios = []
    for d in common_days:
        act = actual[d]
        mod = modelled[d]
        if mod < 0.5:
            note = "(skip — near-zero model, likely bad weather day)"
            print(f"{d}  {act:>12.2f}  {mod:>14.2f}  {'—':>8}  {note}")
            continue
        ratio = act / mod
        ratios.append(ratio)
        flag = " ← outlier" if ratio > 2.5 or ratio < 0.5 else ""
        print(f"{d}  {act:>12.2f}  {mod:>14.2f}  {ratio:>8.2f}{flag}")

    if not ratios:
        print("\nNo valid days for bias calculation.")
        return

    mean_ratio = mean(ratios)
    std_ratio  = stdev(ratios) if len(ratios) > 1 else 0.0
    calibrated_efficiency = EFFICIENCY_BASELINE * mean_ratio

    print()
    print("=" * 65)
    print(f"  Days analysed:            {len(ratios)}")
    print(f"  Mean actual / modelled:   {mean_ratio:.3f}  (stdev: {std_ratio:.3f})")
    print(f"  Baseline efficiency:      {EFFICIENCY_BASELINE:.2f}")
    print(f"  Calibrated efficiency:    {calibrated_efficiency:.3f}")
    print()

    if mean_ratio > 1.1:
        print(f"  ✓ Open-Meteo UNDERestimates by {(mean_ratio - 1)*100:.0f}% on average.")
    elif mean_ratio < 0.9:
        print(f"  ✗ Open-Meteo OVERestimates by {(1 - mean_ratio)*100:.0f}% on average.")
    else:
        print(f"  ✓ Open-Meteo estimate is within 10% — good calibration.")

    print()
    print("  To apply calibration, update openmeteo.py:")
    print(f"    EFFICIENCY = {calibrated_efficiency:.3f}  # was {EFFICIENCY_BASELINE:.2f}")
    print()
    print("  Or add a forecast_calibration_multiplier to config.yaml:")
    print(f"    forecast_calibration_multiplier: {mean_ratio:.3f}")
    print("=" * 65)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Open-Meteo forecast bias analysis")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days",   type=int, default=30, help="Number of past days to analyse")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)

    end   = date.today() - timedelta(days=1)   # yesterday (archive available)
    start = end - timedelta(days=args.days - 1)

    print(f"WattCast — Open-Meteo Forecast Bias Analysis")
    print(f"Period: {start} → {end}  ({args.days} days)")
    print(f"System: {config['panels']['total_kwp']} kWp, "
          f"tilt={config['panels']['tilt']}°, azimuth={config['panels']['azimuth']}°")
    print()

    print("Fetching actual generation from MariaDB...")
    actual = fetch_actual_generation(config, start, end)
    print(f"  Found {len(actual)} days with generation data")

    modelled = fetch_archive_gti(config, start, end)
    print(f"  Got {len(modelled)} days of archive GTI data")

    analyse(actual, modelled)


if __name__ == "__main__":
    main()
