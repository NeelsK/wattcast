-- WattCast local database schema
-- MariaDB / MySQL compatible
-- Run as root: mariadb < schema.sql

CREATE DATABASE IF NOT EXISTS wattcast CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Create user if not exists (password set by install.sh via sed)
CREATE USER IF NOT EXISTS 'wattcast'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON wattcast.* TO 'wattcast'@'localhost';
FLUSH PRIVILEGES;

USE wattcast;

-- ---------------------------------------------------------------------------
-- Weather station readings (Ecowitt push)
-- Matches the relevant subset of the Unraid weather_readings schema
-- so data is compatible when forwarded upstream.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_readings (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    timestamp           DATETIME NOT NULL,
    temp_outdoor        DECIMAL(5,2),
    temp_indoor         DECIMAL(5,2),
    humidity_outdoor    DECIMAL(5,2),
    humidity_indoor     DECIMAL(5,2),
    pressure_absolute   DECIMAL(6,2),
    pressure_relative   DECIMAL(6,2),
    wind_speed          DECIMAL(5,2),
    wind_gust           DECIMAL(5,2),
    wind_direction      INT,
    rain_rate           DECIMAL(6,2),
    rain_event          DECIMAL(6,2),
    rain_hourly         DECIMAL(6,2),
    rain_daily          DECIMAL(6,2),
    rain_weekly         DECIMAL(6,2),
    rain_monthly        DECIMAL(6,2),
    uv_index            DECIMAL(4,2),
    solar_radiation     DECIMAL(7,2),
    dew_point           DECIMAL(5,2),
    wind_chill          DECIMAL(5,2),
    heat_index          DECIMAL(5,2),
    lightning_count     INT UNSIGNED,
    lightning_distance  DECIMAL(5,1),
    lightning_time      DATETIME,
    soil_moisture_1     TINYINT UNSIGNED,
    soil_moisture_2     TINYINT UNSIGNED,
    batt_lightning      TINYINT UNSIGNED,
    batt_soil_1         DECIMAL(3,1),
    batt_wind           DECIMAL(3,1),
    raw_data            TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_timestamp (timestamp),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Solar / inverter readings (mqtt_bridge push, every 60s)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_solar_readings (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    timestamp               DATETIME NOT NULL,
    pv_power                INT,
    battery_power           INT,
    grid_power              INT,
    grid_connected          TINYINT(1),
    load_power              INT,
    battery_soc             INT,
    ac_voltage              DECIMAL(5,1),
    ac_frequency            DECIMAL(4,2),
    pv1_power               INT,
    pv2_power               INT,
    generation_today        DECIMAL(6,2),
    consumption_today       DECIMAL(6,2),
    battery_charge_today    DECIMAL(6,2),
    battery_discharge_today DECIMAL(6,2),
    grid_import_today       DECIMAL(6,2),
    grid_export_today       DECIMAL(6,2),
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Daily weather summary (maintained by webhook receiver)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_daily_summary (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    date                DATE NOT NULL UNIQUE,
    temp_min            DECIMAL(5,2),
    temp_max            DECIMAL(5,2),
    temp_avg            DECIMAL(5,2),
    humidity_min        DECIMAL(5,2),
    humidity_max        DECIMAL(5,2),
    humidity_avg        DECIMAL(5,2),
    pressure_min        DECIMAL(6,2),
    pressure_max        DECIMAL(6,2),
    pressure_avg        DECIMAL(6,2),
    wind_speed_max      DECIMAL(5,2),
    wind_gust_max       DECIMAL(5,2),
    wind_speed_avg      DECIMAL(5,2),
    wind_direction_avg  INT,
    rain_total          DECIMAL(6,2),
    uv_max              DECIMAL(4,2),
    solar_max           DECIMAL(7,2),
    solar_avg           DECIMAL(7,2),
    dew_point_avg       DECIMAL(5,2),
    reading_count       INT DEFAULT 0,
    actual_gen_kwh      DECIMAL(5,1),
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Notification log (Telegram alerts from mqtt_bridge)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_notification_log (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    event_type  VARCHAR(50) NOT NULL,
    message     TEXT NOT NULL,
    sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    telegram_ok TINYINT(1) DEFAULT 1,
    error_msg   VARCHAR(255),
    INDEX idx_event_type (event_type),
    INDEX idx_sent_at (sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Site settings (key/value store for runtime config)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_site_settings (
    setting_key     VARCHAR(64) PRIMARY KEY,
    setting_value   TEXT,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Default settings
INSERT IGNORE INTO weather_site_settings (setting_key, setting_value) VALUES
    ('telegram_enabled', '0'),
    ('telegram_bot_token', ''),
    ('telegram_chat_id', ''),
    ('notify_grid_lost', '1'),
    ('notify_grid_restored', '1'),
    ('notify_battery_low', '1'),
    ('notify_battery_low_threshold', '20'),
    ('notify_battery_recovered', '1'),
    ('notify_battery_recovered_threshold', '50');

-- ---------------------------------------------------------------------------
-- Rule engine — presets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engine_presets (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64) NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS engine_preset_slots (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    preset_id   INT NOT NULL,
    slot_num    TINYINT NOT NULL,          -- 1–6
    time        VARCHAR(5) NOT NULL,       -- "HH:MM"
    capacity    TINYINT NOT NULL,          -- SOC % floor
    grid_charge TINYINT(1) NOT NULL DEFAULT 1,
    UNIQUE KEY uq_preset_slot (preset_id, slot_num),
    FOREIGN KEY (preset_id) REFERENCES engine_presets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Rule engine — rules
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engine_rules (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    name         VARCHAR(64) NOT NULL UNIQUE,
    priority     INT NOT NULL DEFAULT 100,
    preset_name  VARCHAR(64) NOT NULL,
    default_rule TINYINT(1) NOT NULL DEFAULT 0,
    enabled      TINYINT(1) NOT NULL DEFAULT 1,
    description  TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS engine_rule_groups (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    rule_id INT NOT NULL,
    FOREIGN KEY (rule_id) REFERENCES engine_rules(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS engine_rule_conditions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    group_id    INT NOT NULL,
    field       VARCHAR(64) NOT NULL,
    op          VARCHAR(4) NOT NULL,
    value       DECIMAL(10,3) NOT NULL,
    FOREIGN KEY (group_id) REFERENCES engine_rule_groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Geyser control
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geyser_config (
    setting_key   VARCHAR(64) PRIMARY KEY,
    setting_value TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO geyser_config (setting_key, setting_value) VALUES
    ('mode',            'auto'),     -- auto | force_on | force_off
    ('on_soc',          '85'),
    ('off_soc',         '70'),
    ('min_irradiance',  '450');

CREATE TABLE IF NOT EXISTS geyser_schedules (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    time    VARCHAR(5) NOT NULL,    -- "HH:MM"
    action  VARCHAR(8) NOT NULL,    -- "on" | "off"
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
