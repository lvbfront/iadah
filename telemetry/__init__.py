"""Per-step metrics + per-incident MTTD/MTTR to SQLite. Every logged step is also printed
as one row of the live timeline."""
import sqlite3
import time

from rich.console import Console

import config

console = Console()
_conn = None
_t0: dict[str, float] = {}
LISTENERS: list = []  # callables(row) — e.g. the dashboard's live feed

BADGE = {"small": "[black on green] SMALL [/]", "big": "[white on magenta]  BIG  [/]",
         "rules": "[black on white] RULES [/]", "human": "[black on yellow] HUMAN [/]"}


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.TELEMETRY_DB)
        _conn.execute("""CREATE TABLE IF NOT EXISTS steps (
            ts REAL, incident TEXT, stage TEXT, tier TEXT, requested_model TEXT, model TEXT,
            in_tokens INT, out_tokens INT, latency_s REAL, cost_usd REAL, note TEXT)""")
        _conn.execute("""CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, scenario TEXT, mode TEXT, injected_at REAL, detected_at REAL,
            recovered_at REAL, mttd_s REAL, mttr_s REAL, raw_log_tokens INT, compressed_log_tokens INT,
            resolved INT, iterations INT, escalations INT, total_cost REAL, total_tokens INT)""")
    return _conn


def start_incident(inc_id: str, scenario: str, mode: str, t0: float):
    _t0[inc_id] = t0
    with _db() as c:
        c.execute("INSERT INTO incidents (id, scenario, mode, injected_at) VALUES (?,?,?,?)",
                  (inc_id, scenario, mode, t0))


def update_incident(inc_id: str, **fields):
    with _db() as c:
        c.execute(f"UPDATE incidents SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                  (*fields.values(), inc_id))


def log_step(incident: str, stage: str, result=None, latency_s: float = 0.0, note: str = "", tier: str = "rules"):
    """result is an llm.LLMResult, or None for rule/human stages."""
    r = result
    row = (time.time(), incident, stage, r.tier if r else tier, r.requested_model if r else "",
           r.model if r else "", r.in_tokens if r else 0, r.out_tokens if r else 0,
           r.latency_s if r else latency_s, r.cost_usd if r else 0.0, note)
    with _db() as c:
        c.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
    _print(row)
    for f in LISTENERS:
        f(row)


def _print(row):
    ts, inc, stage, tier, req, model, tin, tout, lat, cost, note = row
    t = f"{ts - _t0.get(inc, ts):6.1f}s"
    model_s = model + (f" [red](fallback from {req})[/]" if req and model and model != req else "")
    toks = f"{tin:>5}→{tout:<4}" if tin or tout else " " * 10
    console.print(f"[dim]{t}[/] {stage:<11} {BADGE.get(tier, tier)} {toks} {lat:5.2f}s ${cost:.5f}  "
                  f"[cyan]{model_s}[/] {note}", highlight=False, soft_wrap=True)


def totals(inc_id: str) -> dict:
    rows = _db().execute("""SELECT tier, model, COUNT(*), SUM(in_tokens), SUM(out_tokens), SUM(latency_s),
        SUM(cost_usd) FROM steps WHERE incident=? AND model != '' GROUP BY tier, model""", (inc_id,)).fetchall()
    return {"by_model": rows, "cost": sum(r[6] for r in rows), "tokens": sum(r[3] + r[4] for r in rows)}


def steps(inc_id: str):
    return _db().execute("SELECT ts, stage, tier, model, in_tokens, out_tokens, cost_usd, note FROM steps "
                         "WHERE incident=? ORDER BY ts", (inc_id,)).fetchall()
