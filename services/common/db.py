"""SQLite storage for the prototype. The target design uses PostgreSQL + pgvector.

Truth columns exist only for evaluation. The matching engine must read
`pipeline_crossings` (a view with no truth columns) and `vahan`, nothing else.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (            -- TRUTH. Never read by the pipeline.
  vehicle_id INTEGER PRIMARY KEY, vrn TEXT, make TEXT, model TEXT, colour TEXT,
  body TEXT, axles INTEGER, tag_id TEXT, evader_type TEXT);
CREATE TABLE IF NOT EXISTS vahan (               -- Registration records the pipeline may query.
  vrn TEXT PRIMARY KEY, make TEXT, model TEXT, colour TEXT, body TEXT, axles INTEGER);
CREATE TABLE IF NOT EXISTS trips (               -- TRUTH. Used by the replay screen and evaluation.
  trip_id INTEGER PRIMARY KEY, vehicle_id INTEGER, day INTEGER, direction INTEGER,
  start_ts REAL, end_ts REAL, start_km REAL, end_km REAL);
CREATE TABLE IF NOT EXISTS crossings (
  event_id INTEGER PRIMARY KEY, plaza_id TEXT, plaza_type TEXT, direction INTEGER, ts REAL,
  rfid_tag_id TEXT, anpr_text TEXT, plate_status TEXT,
  obs_make TEXT, obs_model TEXT, obs_colour TEXT, obs_body TEXT,
  system_class TEXT,
  trip_id INTEGER, truth_vehicle_id INTEGER, ghost_kind TEXT);   -- last three: TRUTH
CREATE VIEW IF NOT EXISTS pipeline_crossings AS
  SELECT event_id, plaza_id, plaza_type, direction, ts, rfid_tag_id, anpr_text, plate_status,
         obs_make, obs_model, obs_colour, obs_body, system_class
  FROM crossings;
CREATE TABLE IF NOT EXISTS candidates (
  case_id INTEGER, anchor_event_id INTEGER, vrn TEXT, plaza_id TEXT,
  dt_minutes REAL, offset_minutes REAL,
  sim_optimistic REAL, sim_central REAL, sim_pessimistic REAL);
CREATE TABLE IF NOT EXISTS cases (
  case_id INTEGER PRIMARY KEY, ghost_event_id INTEGER, decision TEXT,
  top_vrn TEXT, top_score REAL, runner_up_score REAL, n_candidates INTEGER,
  top_plaza_id TEXT, reviewer_decision TEXT, reviewer_id TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, actor TEXT,
  action TEXT, ts TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_cross_type ON crossings(plaza_type, system_class);
CREATE INDEX IF NOT EXISTS ix_cross_trip ON crossings(trip_id);
CREATE INDEX IF NOT EXISTS ix_cross_veh ON crossings(truth_vehicle_id);
CREATE INDEX IF NOT EXISTS ix_cand_case ON candidates(case_id);
CREATE INDEX IF NOT EXISTS ix_trips_time ON trips(start_ts, end_ts);
"""


def connect(path: Path, create: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    if create:
        con.executescript(SCHEMA)
    return con


def put(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (key, value))
    con.commit()


def get(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
    return row[0] if row else None
