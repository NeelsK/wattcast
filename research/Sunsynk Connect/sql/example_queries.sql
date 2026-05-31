-- Useful queries against the sunsynk.db logger.
-- Run with: sqlite3 ~/sunsynk.db < sql/example_queries.sql

-- =============================================================
-- Daily totals (last 30 days)
-- =============================================================
SELECT
    date(ts, 'unixepoch', 'localtime')        AS day,
    MAX(day_pv_energy)                        AS pv_kwh,
    MAX(day_battery_charge)                   AS bat_charged_kwh,
    MAX(day_battery_discharge)                AS bat_discharged_kwh,
    MAX(day_grid_import)                      AS grid_import_kwh,
    MAX(day_grid_export)                      AS grid_export_kwh,
    MAX(day_load_energy)                      AS load_kwh
FROM samples
WHERE ts >= strftime('%s', 'now', '-30 days')
GROUP BY day
ORDER BY day DESC;

-- =============================================================
-- Today's power profile (1-minute resolution)
-- =============================================================
SELECT
    time(ts, 'unixepoch', 'localtime')        AS time_local,
    battery_soc,
    pv1_power + pv2_power                     AS pv_w,
    load_power,
    grid_power,
    battery_power
FROM samples
WHERE date(ts, 'unixepoch', 'localtime') = date('now', 'localtime')
ORDER BY ts;

-- =============================================================
-- Self-sufficiency by day (PV used directly + from battery, vs total load)
-- =============================================================
SELECT
    date(ts, 'unixepoch', 'localtime')        AS day,
    MAX(day_load_energy)                      AS load_kwh,
    MAX(day_grid_import)                      AS imported_kwh,
    ROUND(
        100.0 * (MAX(day_load_energy) - MAX(day_grid_import))
        / NULLIF(MAX(day_load_energy), 0),
    1)                                        AS self_sufficiency_pct
FROM samples
WHERE ts >= strftime('%s', 'now', '-30 days')
GROUP BY day
ORDER BY day DESC;

-- =============================================================
-- Battery cycles (rough): kWh discharged / nominal capacity
-- Replace 5.12 with your battery's actual usable kWh
-- =============================================================
SELECT
    date(ts, 'unixepoch', 'localtime')        AS day,
    ROUND(MAX(day_battery_discharge) / 5.12, 2) AS approx_cycles
FROM samples
WHERE ts >= strftime('%s', 'now', '-90 days')
GROUP BY day
ORDER BY day DESC;

-- =============================================================
-- Periods where you were importing from grid while battery was above 50%
-- (i.e. potentially incorrect work mode / TOU schedule)
-- =============================================================
SELECT
    datetime(ts, 'unixepoch', 'localtime')    AS when_local,
    battery_soc,
    grid_power,
    load_power
FROM samples
WHERE grid_power > 100
  AND battery_soc > 50
  AND ts >= strftime('%s', 'now', '-7 days')
ORDER BY ts DESC
LIMIT 50;
