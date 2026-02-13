PRAGMA foreign_keys = ON;

-- -----------------------------
-- Locomotive speed calibration history
-- -----------------------------
CREATE TABLE IF NOT EXISTS loco (
  loco_id      TEXT PRIMARY KEY,
  display_name TEXT,
  dcc_address  TEXT,
  roster_file  TEXT,
  roster_id    TEXT,
  created_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  updated_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS speed_run (
  run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  loco_id      TEXT NOT NULL,
  run_ts       INTEGER NOT NULL,
  direction    TEXT NOT NULL,            -- FWD|REV
  run_mode     TEXT NOT NULL,            -- FULL|FWD|REV|VERIFY
  notes        TEXT,
  created_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  FOREIGN KEY(loco_id) REFERENCES loco(loco_id)
);

CREATE INDEX IF NOT EXISTS idx_speed_run_loco_ts ON speed_run(loco_id, run_ts);

-- Raw per-sample measurements captured during automation
CREATE TABLE IF NOT EXISTS speed_sample (
  sample_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER NOT NULL,
  sample_ts    INTEGER NOT NULL,
  direction    TEXT NOT NULL,            -- FWD|REV
  speed_step   INTEGER NOT NULL,
  mode         TEXT NOT NULL,            -- LAP|SEG1|SEG4
  mph          REAL NOT NULL,
  FOREIGN KEY(run_id) REFERENCES speed_run(run_id)
);

CREATE INDEX IF NOT EXISTS idx_speed_sample_run_step ON speed_sample(run_id, speed_step);

-- Aggregated per-step summary after 7 samples (MAD filtered)
CREATE TABLE IF NOT EXISTS speed_step_summary (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER NOT NULL,
  direction    TEXT NOT NULL,
  speed_step   INTEGER NOT NULL,
  mode         TEXT NOT NULL,
  mean_mph     REAL NOT NULL,
  kept_n       INTEGER NOT NULL,
  total_n      INTEGER NOT NULL,
  mad          REAL,
  sigma_est    REAL,
  moe          REAL,
  FOREIGN KEY(run_id) REFERENCES speed_run(run_id)
);

CREATE INDEX IF NOT EXISTS idx_speed_step_summary_run_step ON speed_step_summary(run_id, speed_step);

-- Optional: CV changes (if/when you add CV writing)
CREATE TABLE IF NOT EXISTS cv_change (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER NOT NULL,
  cv           INTEGER NOT NULL,
  old_value    INTEGER,
  new_value    INTEGER,
  direction    TEXT,
  notes        TEXT,
  created_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  FOREIGN KEY(run_id) REFERENCES speed_run(run_id)
);

-- -----------------------------
-- Cars inventory extensions
-- -----------------------------
CREATE TABLE IF NOT EXISTS car_ext (
  car_key      TEXT PRIMARY KEY,         -- ROAD_NUMBER_TYPE
  road         TEXT,
  number       TEXT,
  car_type     TEXT,
  notes        TEXT,
  created_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  updated_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS car_maintenance_event (
  event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  car_key      TEXT NOT NULL,
  event_ts     INTEGER NOT NULL,
  category     TEXT,
  summary      TEXT NOT NULL,
  details      TEXT,
  created_ts   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  FOREIGN KEY(car_key) REFERENCES car_ext(car_key)
);

CREATE INDEX IF NOT EXISTS idx_car_maint_car_ts ON car_maintenance_event(car_key, event_ts);

CREATE TABLE IF NOT EXISTS car_media (
  media_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  car_key      TEXT NOT NULL,
  event_id     INTEGER,
  media_type   TEXT NOT NULL,             -- photo|doc|sheet
  file_path    TEXT NOT NULL,             -- relative to data root
  caption      TEXT,
  taken_ts     INTEGER,
  imported_ts  INTEGER NOT NULL,
  source       TEXT,
  sha256       TEXT,
  FOREIGN KEY(car_key) REFERENCES car_ext(car_key),
  FOREIGN KEY(event_id) REFERENCES car_maintenance_event(event_id)
);

CREATE INDEX IF NOT EXISTS idx_car_media_car ON car_media(car_key);

-- Simple key/value
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

INSERT OR IGNORE INTO kv(k, v) VALUES ('schema_version', '2');

-- -----------------------------------------
-- Full derived "Results Spreadsheet" per run
-- (persisted when Calculate/Open Results runs)
-- -----------------------------------------
CREATE TABLE IF NOT EXISTS speed_run_results_row (
  run_id            INTEGER NOT NULL,
  step              INTEGER NOT NULL,
  target_mph        REAL,
  fwd_measured_mph  REAL,
  fwd_error_mph     REAL,
  fwd_pct_dev       REAL,
  fwd_moe           REAL,
  fwd_cv_value      INTEGER,
  current_cv_value  INTEGER,
  rev_measured_mph  REAL,
  rev_error_mph     REAL,
  rev_pct_dev       REAL,
  rev_moe           REAL,
  PRIMARY KEY (run_id, step),
  FOREIGN KEY (run_id) REFERENCES speed_run(run_id) ON DELETE CASCADE
);
