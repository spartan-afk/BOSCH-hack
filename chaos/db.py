"""results.db: everything loadgen and observer measure. One shared connection, locked."""

import sqlite3
import threading

from chaos.config import DATA_DIR

DB_PATH = DATA_DIR / "results.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, kind TEXT, started_at REAL, ended_at REAL);
CREATE TABLE IF NOT EXISTS sends (          -- loadgen log
    run_id TEXT, phase TEXT, vehicle TEXT, seq INTEGER, sent_at REAL,
    done_at REAL, status INTEGER, error TEXT,
    PRIMARY KEY (vehicle, seq));
CREATE TABLE IF NOT EXISTS samples (        -- observer, one row per vehicle per second
    run_id TEXT, phase TEXT, ts REAL, vehicle TEXT, status INTEGER,
    latency_ms REAL, seq INTEGER, data_age_s REAL, server_age_s REAL, error TEXT);
CREATE TABLE IF NOT EXISTS containers (
    run_id TEXT, ts REAL, name TEXT, service TEXT, state TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT, ts REAL, type TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS samples_run ON samples (run_id, phase);
CREATE INDEX IF NOT EXISTS sends_run ON sends (run_id, phase);
"""


class RunState:
    """Shared between orchestrator, loadgen and observer: which run and phase we are in."""

    def __init__(self, run_id: str, phase: str = "before"):
        self.run_id = run_id
        self.phase = phase


class ResultsDB:
    def __init__(self, path=DB_PATH):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def execute(self, sql: str, params=()) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def query(self, sql: str, params=()) -> list[tuple]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def event(self, run_id: str, ts: float, type_: str, detail: str = "") -> None:
        self.execute("INSERT INTO events VALUES (?,?,?,?)", (run_id, ts, type_, detail))

    def close(self) -> None:
        with self.lock:
            self.conn.close()
