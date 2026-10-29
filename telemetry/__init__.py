"""Per-step metrics to SQLite."""
import sqlite3
import time

import config

_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.TELEMETRY_DB)
        _conn.execute("""CREATE TABLE IF NOT EXISTS steps (
            ts REAL, incident TEXT, stage TEXT, tier TEXT, model TEXT,
            in_tokens INT, out_tokens INT, latency_s REAL, cost_usd REAL, note TEXT)""")
    return _conn


def log_step(incident: str, stage: str, result=None, latency_s: float = 0.0, note: str = ""):
    """result is an llm.LLMResult, or None for rule-only stages."""
    r = result
    with _db() as c:
        c.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?,?)", (
            time.time(), incident, stage,
            r.tier if r else "rules", r.model if r else "",
            r.in_tokens if r else 0, r.out_tokens if r else 0,
            r.latency_s if r else latency_s, r.cost_usd if r else 0.0, note))
