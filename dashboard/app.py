"""Live demo dashboard: inject a fault, watch the agent recover it, approve high-risk actions in the page.

uv run python -m dashboard.app        → http://localhost:8080

Read-only view of the agent: it runs the same `agent.run.run()` as the CLI. The only hooks are
`telemetry.LISTENERS` (live step feed) and `stages.approver` (web Approve/Deny instead of y/n).
Live runs are recorded to .state/dashboard/<incident>.jsonl so they can be replayed at real speed.
"""
import asyncio
import csv
import json
import re
import threading
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import chaos
import config
import telemetry
from adapters.docker_adapter import DockerAdapter
from agent import run as runner
from agent import runbooks, stages
from agent.state import Incident
from benchmarks.run import mean, pct

HERE = Path(__file__).parent
RECORDINGS = Path(config.STATE_DIR) / "dashboard"
SCENARIOS = {k: d for k, (d, _) in chaos.SCENARIOS.items() if k != "reset"}


class Bus:
    """Step log for the current activity (`gen` bumps when a new run/replay starts) plus the latest state."""
    def __init__(self):
        self.lock, self.gen, self.events, self.rec = threading.Lock(), 0, [], None
        self.state, self.ver = None, 0

    def reset(self, **info):
        with self.lock:
            self.gen += 1
            self.events = [{"type": "reset", **info}]
            self.state = None  # never send the previous activity's state after a reset

    def push(self, ev: dict, record=True):
        with self.lock:
            if ev["type"] == "state":
                self.state, self.ver = ev, self.ver + 1
            else:
                self.events.append(ev)
            if record and self.rec is not None:
                self.rec.append({"wall": time.time(), **ev})

    def since(self, gen, idx, ver):
        with self.lock:
            evs = self.events[idx:] if gen == self.gen else list(self.events)
            return self.gen, evs, (self.state if ver != self.ver else None), self.ver


bus = Bus()
S = {"activity": "idle", "inc": None, "mode": "hybrid", "approval": None, "replay": None, "error": ""}
adapter = DockerAdapter()
agent_thread = ThreadPoolExecutor(1)  # one persistent thread: telemetry's sqlite connection is per-thread
_approval_lock = threading.Lock()


@dataclass
class Tracked(Incident):
    """Same Incident; just lets the dashboard see the live object run() creates."""
    def __post_init__(self):
        S["inc"] = self


runner.Incident = Tracked


def _ro():
    return sqlite3.connect(config.TELEMETRY_DB)  # own connection: telemetry's belongs to the agent thread


MARKUP = re.compile(r"\[/?(?:(?:bold|dim|italic|red|green|yellow|cyan|magenta|white|black|blue|on)\s*)*\]")


def plain(note: str) -> str:
    """Strip rich style tags only; keep Python lists like ['cache'] that notes also contain."""
    return MARKUP.sub("", note)


def on_step(row):
    ts, inc, stage, tier, req, model, tin, tout, lat, cost, note = row
    t0 = telemetry._t0.get(inc, ts)
    bus.push({"type": "step", "t": round(ts - t0, 1), "stage": stage, "tier": tier, "model": model,
              "fallback": bool(req and model and model != req), "tin": tin, "tout": tout,
              "lat": round(lat, 2), "cost": cost, "note": plain(note)})


telemetry.LISTENERS.append(on_step)


def web_approver(action, svc, reason) -> bool:
    ev = threading.Event()
    with _approval_lock:
        S["approval"] = {"id": time.time(), "action": action, "service": svc, "reason": reason, "event": ev, "yes": False}
    answered = ev.wait(600)  # nobody answered in 10 min → deny (fail safe)
    with _approval_lock:
        yes, S["approval"] = S["approval"]["yes"] and answered, None
    return yes


stages.approver = web_approver


def snapshot() -> dict:
    inc, now = S["inc"], time.time()
    health = adapter.get_health()
    topo = adapter.get_topology()
    flagged = stages.flags(health, inc.baseline if inc else {})
    nodes = {}
    live = S["activity"] in ("running", "done") and inc
    roots = {r["service"]: r["incident_type"] for r in (inc.roots if inc else [])}
    for s, h in health.items():
        state = "fault" if s in flagged else "healthy"
        if live and inc.injected_at and not inc.resolved:
            if s in roots or (not roots and s in inc.suspects):
                state = "root"
            elif s in inc.symptoms:
                state = "symptom"
        if S["activity"] == "running" and inc and not inc.injected_at:
            state = "preparing"
        nodes[s] = {"state": state, "status": h["status"], "why": flagged.get(s, ""), "type": roots.get(s, ""),
                    "mem": h.get("mem_pct"), "err": (h.get("app") or {}).get("error_rate")}
    snap = {"type": "state", "activity": S["activity"], "mode": S["mode"], "error": S["error"],
            "topology": topo, "nodes": nodes,
            "approval": {k: v for k, v in S["approval"].items() if k not in ("event", "yes")} if S["approval"] else None}
    if inc:
        end = inc.recovered_at or (S.get("ended") if S["activity"] == "done" else None) or now
        snap["inc"] = {"id": inc.id, "scenario": inc.scenario, "injected": bool(inc.injected_at),
                       "elapsed": round(end - inc.injected_at, 1) if inc.injected_at else 0,
                       "mttd": round(inc.detected_at - inc.injected_at, 1) if inc.detected_at else None,
                       "mttr": round(inc.recovered_at - inc.injected_at, 1) if inc.recovered_at else None,
                       "escalations": inc.escalations, "iteration": inc.iteration, "resolved": inc.resolved,
                       "needs_human": inc.needs_human, "fast_path": inc.fast_path, "plan_source": inc.plan_source,
                       "runbook_saved": inc.runbook_saved, "log_raw": inc.raw_log_tokens,
                       "log_comp": inc.compressed_log_tokens, "roots": inc.roots}
    return snap


def state_loop():
    last = None
    while True:
        try:
            if S["activity"] != "replay":
                snap = snapshot()
                key = json.dumps(snap, sort_keys=True, default=str)
                if key != last or S["activity"] == "running":
                    bus.push(snap)
                    last = key
        except Exception as e:  # docker hiccup: keep the page alive
            S["error"] = f"state: {e}"
        time.sleep(0.5)


def _run(scenario, mode):
    try:
        config.MODE, config.AUTO_APPROVE = mode, False
        inc = runner.run(scenario, adapter)
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        (RECORDINGS / f"{inc.id}.jsonl").write_text("\n".join(json.dumps(e, default=str) for e in bus.rec))
    except BaseException as e:  # SystemExit from wait_healthy too
        S["error"] = f"run failed: {e}"
    finally:
        S["ended"], S["activity"], bus.rec = time.time(), "done", None


def _replay(events):
    try:
        prev = events[0]["wall"]
        for ev in events:
            if S["replay"] != "on":
                return
            time.sleep(max(0.0, ev["wall"] - prev))
            prev = ev["wall"]
            bus.push({k: v for k, v in ev.items() if k != "wall"} | ({"activity": "replay"} if ev["type"] == "state" else {}),
                     record=False)
    finally:
        S["activity"], S["replay"] = "idle", None


# ---------- API ----------

app = FastAPI()


class RunReq(BaseModel):
    scenario: str
    mode: str = "hybrid"


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/events")
async def events():
    async def gen():
        g, idx, ver = -1, 0, -1
        while True:
            ng, evs, st, ver = bus.since(g, idx, ver)
            if ng != g:
                g, idx = ng, 0
            for e in evs + ([st] if st else []):
                yield f"data: {json.dumps(e, default=str)}\n\n"
            idx += len(evs)
            await asyncio.sleep(0.15)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/meta")
def meta():
    return {"scenarios": SCENARIOS, "modes": ["hybrid", "big-only", "small-only"]}


def _busy():
    if S["activity"] in ("running", "replay"):
        raise HTTPException(409, f"busy: {S['activity']}")


@app.post("/api/run")
def start(req: RunReq):
    _busy()
    if req.scenario not in SCENARIOS or req.mode not in meta()["modes"]:
        raise HTTPException(400, "bad scenario or mode")
    S.update(activity="running", mode=req.mode, inc=None, error="", approval=None, ended=None)
    bus.reset(kind="live", scenario=req.scenario, desc=SCENARIOS[req.scenario], mode=req.mode)
    bus.rec = []
    agent_thread.submit(_run, req.scenario, req.mode)
    return {"ok": True}


@app.post("/api/approval/{answer}")
def answer(answer: str):
    with _approval_lock:
        a = S["approval"]
        if not a or answer not in ("approve", "deny"):
            raise HTTPException(409, "no pending approval")
        a["yes"] = answer == "approve"
        a["event"].set()
    return {"ok": True}


@app.get("/api/runbooks")
def rb_list():
    return runbooks.listing()


RB_OPS = {"approve": runbooks.approve, "hide": runbooks.hide, "delete": runbooks.delete,
          "restore": lambda _: runbooks.restore()}


@app.post("/api/runbooks/{op}")
def rb_op(op: str, name: str = ""):
    _busy()
    if op not in RB_OPS:
        raise HTTPException(404, "unknown op")
    try:
        return {"ok": RB_OPS[op](name)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/incidents")
def incidents():
    with _ro() as c:
        rows = c.execute("SELECT id, scenario, mode, mttr_s, resolved, total_cost, escalations FROM incidents "
                         "ORDER BY injected_at DESC LIMIT 40").fetchall()
    return [{"id": r[0], "scenario": r[1], "mode": r[2], "mttr": r[3], "resolved": r[4], "cost": r[5],
             "escalations": r[6], "recorded": (RECORDINGS / f"{r[0]}.jsonl").exists()} for r in rows]


@app.post("/api/replay/{inc_id}")
def replay(inc_id: str):
    _busy()
    f = RECORDINGS / f"{inc_id}.jsonl"
    if f.exists():
        evs = [json.loads(line) for line in f.read_text().splitlines()]
        first = next(i for i, e in enumerate(evs) if e["type"] == "step")  # skip reset + baseline wait
        evs = evs[max(0, first - 1):]
    else:  # CLI/benchmark incident: timeline and counters only, no topology recording
        with _ro() as c:
            row = c.execute("SELECT injected_at, scenario, mode, mttd_s, mttr_s, escalations, resolved "
                            "FROM incidents WHERE id=?", (inc_id,)).fetchone()
            steps = c.execute("SELECT ts, incident, stage, tier, requested_model, model, in_tokens, out_tokens, "
                              "latency_s, cost_usd, note FROM steps WHERE incident=? ORDER BY ts", (inc_id,)).fetchall()
        if not row or not steps:
            raise HTTPException(404, "no such incident")
        t0, evs = row[0], []
        for s in steps:
            evs.append({"wall": s[0], "type": "step", "t": round(s[0] - t0, 1), "stage": s[2], "tier": s[3],
                        "model": s[5], "fallback": bool(s[4] and s[5] and s[4] != s[5]), "tin": s[6], "tout": s[7],
                        "lat": round(s[8], 2), "cost": s[9], "note": plain(s[10])})
            last = s is steps[-1]
            evs.append({"wall": s[0], "type": "state", "nodes": {}, "topology": {}, "approval": None, "error": "",
                        "inc": {"id": inc_id, "injected": True, "elapsed": round(s[0] - t0, 1),
                                "mttd": row[3] if s[0] - t0 >= (row[3] or 1e9) else None,
                                "mttr": row[4] if last else None, "escalations": row[5] if last else 0,
                                "resolved": bool(row[6]) and last, "needs_human": ""}})
    S.update(activity="replay", replay="on", inc=None)
    bus.reset(kind="replay", incident=inc_id, recorded=f.exists())
    threading.Thread(target=_replay, args=(evs,), daemon=True).start()
    return {"ok": True}


@app.post("/api/replay-stop")
def replay_stop():
    S["replay"] = "off"
    return {"ok": True}


@app.get("/api/benchmark")
def benchmark():
    v2 = list(csv.DictReader(open("benchmarks/results.csv")))
    v1 = [r for r in csv.DictReader(open("benchmarks/v1/results.csv")) if r["invalid"] != "1"]
    v2 = [r for r in v2 if r["invalid"] != "1"]
    modes = [(m, c) for m in ("hybrid", "big-only", "small-only") for c in ("cold", "warm")]
    pick = lambda rs, m, c, sc: [r for r in rs if r["mode"] == m and r["condition"] == c and r["scenario"] in sc]

    def row(rs, cr=True):
        return {"runs": len(rs), "diag": pct(r["diag_correct"] for r in rs), "recovered": pct(r["resolved"] for r in rs),
                "cr": pct(r["correct_and_recovered"] for r in rs) if cr else "",
                "mttr": mean(r["mttr_s"] for r in rs if r["resolved"] == "1"),
                "unnecessary": mean((r["unnecessary_actions"] or 0 for r in rs), 2) if cr else "",
                "esc": mean((r["escalations"] or 0 for r in rs), 2), "cost": mean((r["cost_usd"] or 0 for r in rs), 4),
                "total": round(sum(float(r["cost_usd"] or 0) for r in rs), 3)}
    return {
        "v2": [{"mode": m, "cond": c, **row(pick(v2, m, c, "123456"))} for m, c in modes],
        "compare": [{"mode": m, "cond": c, "v1": row(pick(v1, m, c, "12345"), cr=False), "v2": row(pick(v2, m, c, "12345"))}
                    for m, c in modes],
        "control": [{"mode": m, "runs": len(rs), "detections": sum(int(r["detected"] or 0) for r in rs),
                     "actions": sum(int(r["actions"] or 0) for r in rs)}
                    for m in ("hybrid", "big-only", "small-only") for rs in [[r for r in v2 if r["scenario"] == "0" and r["mode"] == m]]],
        "s6": [{"mode": m, "cond": c, **row(pick(v2, m, c, "6"))} for m, c in modes],
        "totals": {"runs": len(v2), "violations": sum(1 for r in v2 if r["invariant_violations"]),
                   "spend": round(sum(float(r["cost_usd"] or 0) for r in v2), 2)},
    }


if __name__ == "__main__":
    threading.Thread(target=state_loop, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
