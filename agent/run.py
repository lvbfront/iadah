"""CLI runner: inject a fault, run the agent, print a live timeline.

uv run python -m agent.run --scenario 1 [--auto-approve] [--mode hybrid|big-only|small-only]
Without --scenario it runs one pass against the system's current state.
"""
import argparse
import time

from rich.table import Table

import chaos
import config
import telemetry
from adapters.docker_adapter import DockerAdapter
from agent import stages
from agent.state import Incident

console = telemetry.console


def wait_healthy(adapter, timeout=90) -> dict:
    """Return a health snapshot once everything is healthy with ~0 errors (resets chaos if needed)."""
    reset = False
    t = time.time()
    while time.time() - t < timeout:
        h = adapter.get_health()
        if all(x["status"] in stages.OK_STATUS and (x.get("app") or {}).get("error_rate", 0) <= 0.05
               for x in h.values()):
            return h
        if not reset:
            console.print("[yellow]system not healthy, running chaos reset…[/]")
            chaos.reset()
            reset = True
        time.sleep(2)
    raise SystemExit("system did not become healthy; check `docker compose ps` in demo/")


def capture_baseline(adapter, samples=3) -> dict:
    acc = {}
    for _ in range(samples):
        for s, h in adapter.get_health().items():
            if "error_rate" in (h.get("app") or {}):
                acc.setdefault(s, []).append(h["app"])
        time.sleep(1)
    return {s: {k: round(sum(x[k] for x in v) / len(v), 3) for k in ("error_rate", "avg_latency_ms")}
            for s, v in acc.items()}


def run(scenario: str | None, adapter=None) -> Incident:
    adapter = adapter or DockerAdapter()
    inc = Incident(id=time.strftime("inc-%Y%m%d-%H%M%S"), scenario=scenario or "")
    if scenario:
        chaos.reset()  # deterministic start: no leftover replicas, configs or chaos containers
        health = wait_healthy(adapter)
        inc.baseline = capture_baseline(adapter)
        for s in health:
            adapter.mark_known_good(s)
        console.print(f"[dim]baseline {inc.baseline}; last-known-good configs saved[/]")
    console.rule(f"[bold]{inc.id}[/]  scenario={scenario or 'live'}  mode={config.MODE}")

    if scenario:
        desc, inject = chaos.SCENARIOS[scenario]
        inject()
    inc.injected_at = time.time()
    telemetry.start_incident(inc.id, inc.scenario, config.MODE, inc.injected_at)
    if scenario:
        telemetry.log_step(inc.id, "inject", note=f"chaos scenario {scenario}: {desc}")

    # detect: poll rules until something is wrong
    window = config.CONTROL_WINDOW_S if scenario == "0" else config.DETECT_WINDOW_S
    while not stages.detect(inc, adapter, log=False):
        if not scenario or time.time() - inc.injected_at > window:
            telemetry.log_step(inc.id, "detect", note=f"[green]no alert in {window}s[/]: system healthy, nothing to do")
            return inc
        time.sleep(1)
    inc.detected_at = time.time()
    # Alert grouping: symptoms trail their root causes by a moment; group them into one incident.
    telemetry.log_step(inc.id, "detect", note=f"first alert {inc.unhealthy}; grouping for {config.ALERT_GROUP_S}s")
    time.sleep(config.ALERT_GROUP_S)
    stages.detect(inc, adapter, merge=True)

    stages.compress_logs(inc, adapter)
    stages.gather(inc, adapter)
    stages.triage(inc, adapter)
    while inc.iteration < config.MAX_VERIFY_ITERATIONS:
        inc.iteration += 1
        if inc.iteration > 1:
            console.rule(f"[yellow]iteration {inc.iteration}[/]", style="yellow")
            stages.detect(inc, adapter)  # refresh unhealthy/suspects after the failed fix
            stages.gather(inc, adapter)
        stages.route(inc, adapter)
        if not stages.fast_path(inc):
            stages.investigate(inc, adapter)
            stages.diagnose(inc, adapter)
        for stage in (stages.plan, stages.act, stages.verify):
            inc.stage = stage.__name__
            stage(inc, adapter)
            if inc.needs_human:
                break
        if inc.resolved or inc.needs_human:
            break
    stages.postmortem(inc, adapter)
    summary(inc)
    return inc


def summary(inc: Incident):
    tot = telemetry.totals(inc.id)
    mttd = inc.detected_at - inc.injected_at
    mttr = f"{inc.recovered_at - inc.injected_at:.1f}s" if inc.recovered_at else "—"
    t = Table(title=f"{'[green]RESOLVED' if inc.resolved else '[red]NOT RESOLVED'}[/]  {inc.id}", show_lines=False)
    for col in ("MTTD", "MTTR", "iterations", "escalations", "LLM tokens", "cost", "log tokens raw→compressed"):
        t.add_column(col, justify="right")
    t.add_row(f"{mttd:.1f}s", mttr, str(inc.iteration), str(inc.escalations), str(tot["tokens"]),
              f"${tot['cost']:.5f}", f"{inc.raw_log_tokens}→{inc.compressed_log_tokens}")
    console.print(t)
    for tier, model, n, tin, tout, lat, cost in tot["by_model"]:
        console.print(f"  {telemetry.BADGE[tier]} {model}: {n} calls, {tin}→{tout} tok, {lat:.1f}s, ${cost:.5f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=[k for k in chaos.SCENARIOS if k != "reset"])
    ap.add_argument("--auto-approve", action="store_true", help="approve high-risk actions without asking")
    ap.add_argument("--mode", choices=["hybrid", "big-only", "small-only"], default="hybrid")
    args = ap.parse_args()
    config.MODE, config.AUTO_APPROVE = args.mode, args.auto_approve
    run(args.scenario)
