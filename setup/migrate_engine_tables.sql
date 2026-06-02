USE wattcast;

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
    slot_num    TINYINT NOT NULL,
    time        VARCHAR(5) NOT NULL,
    capacity    TINYINT NOT NULL,
    grid_charge TINYINT(1) NOT NULL DEFAULT 1,
    UNIQUE KEY uq_preset_slot (preset_id, slot_num),
    FOREIGN KEY (preset_id) REFERENCES engine_presets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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

CREATE TABLE IF NOT EXISTS geyser_config (
    setting_key   VARCHAR(64) PRIMARY KEY,
    setting_value TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO geyser_config (setting_key, setting_value) VALUES
    ('mode',           'auto'),
    ('on_soc',         '85'),
    ('off_soc',        '70'),
    ('min_irradiance', '450');

CREATE TABLE IF NOT EXISTS geyser_schedules (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    time       VARCHAR(5) NOT NULL,
    action     VARCHAR(8) NOT NULL,
    enabled    TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
