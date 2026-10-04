"""SQLite storage: experiments (strategy variants), trades, equity history, agent log, AI spend."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    note TEXT,
    is_champion INTEGER NOT NULL DEFAULT 0,
    retired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    variant_id TEXT NOT NULL,
    mode TEXT NOT NULL,           -- paper | live
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,           -- BUY | SELL
    qty REAL NOT NULL,
    price REAL NOT NULL,
    notional REAL NOT NULL,
    fee REAL NOT NULL,
    pnl REAL,                     -- realized, on SELL
    reason TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    ts REAL NOT NULL,
    variant_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS equity_variant ON equity(variant_id, ts);
CREATE TABLE IF NOT EXISTS agent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # key/value state (mode, kill switch, budget counters)
    def get(self, key: str, default=None):
        rows = self.query("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def log(self, agent: str, level: str, message: str) -> dict:
        ts = time.time()
        self.execute("INSERT INTO agent_log(ts,agent,level,message) VALUES(?,?,?,?)", (ts, agent, level, message))
        return {"ts": ts, "agent": agent, "level": level, "message": message}
