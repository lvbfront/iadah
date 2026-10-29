"""Benchmark: every scenario × mode × condition × N reps. Resumable (skips keys already in the CSV),
unattended (auto-approve, chaos reset before each run, errors recorded and skipped).

uv run python -m benchmarks.run [--n 3] [--scenarios 1,2,3,4,5] [--modes hybrid,big-only,small-only]
                                [--conditions cold,warm] [--report-only]

cold = no runbooks; warm = the seed runbooks. Each run gets a fresh runbook dir so learning never leaks
between runs. Ground truth = chaos.TRUTH.
"""
import argparse
import csv
import shutil
import statistics
import tempfile
import time
import traceback
from pathlib import Path

import yaml

import chaos
import config

OUT = Path(__file__).parent
RESULTS = OUT / "results.csv"
FIELDS = ["key", "scenario", "mode", "condition", "rep", "incident", "detected", "resolved", "still_ok",
          "diag_correct", "correct_and_recovered", "diagnosed", "actions", "unnecessary_actions", "actions_taken",
          "mttd_s", "mttr_s", "iterations", "escalations", "tokens", "big_tokens", "cost_usd", "invariant_violations",
          "sleep_gap_s", "invalid", "error"]
STILL_OK_AFTER_S = 10   # re-check health this long after the agent declares recovery
SLEEP_GAP_LIMIT_S = 60  # wall clock ahead of the monotonic clock by more than this = machine slept → rerun
MAX_ATTEMPTS = 3


def done_keys() -> set[str]:
    """Keys with a valid row; invalid (slept) rows don't count, so they're rerun on resume."""
    if not RESULTS.exists():
        return set()
    with RESULTS.open() as f:
        return {r["key"] for r in csv.DictReader(f) if r["invalid"] != "1"}


def append(row: dict):
    new = not RESULTS.exists()
    with RESULTS.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def runbook_dir(condition: str) -> str:
    d = Path(tempfile.mkdtemp(prefix=f"runbooks-{condition}-"))
    if condition == "warm":
        for p in Path("runbooks").glob("*.yaml"):
            if yaml.safe_load(p.read_text()).get("source") == "seed":
                shutil.copy(p, d)
    return str(d)


def one(scenario: str, mode: str, condition: str, rep: int) -> dict:
    import telemetry
    from adapters.docker_adapter import DockerAdapter
    from agent import stages
    from agent.run import run
    key = f"s{scenario}-{mode}-{condition}-{rep}"
    row = {"key": key, "scenario": scenario, "mode": mode, "condition": condition, "rep": rep, "error": ""}
    config.MODE, config.AUTO_APPROVE = mode, True
    config.RUNBOOKS_DIR = runbook_dir(condition)
    log = (OUT / "logs" / f"{key}.log").open("w")
    telemetry.console.file = log
    # macOS: perf_counter stops while the machine sleeps, time.time() doesn't → the difference is sleep time
    wall0, mono0 = time.time(), time.perf_counter()
    try:
        adapter = DockerAdapter()
        inc = run(scenario, adapter)
        tot = telemetry.totals(inc.id)
        roots = {(r["incident_type"], r["service"]) for r in inc.roots}
        detected = bool(inc.detected_at)
        acts = [(a["action"], a["service"]) for a in inc.actions_taken]
        truth_svcs = {svc for _, svc in chaos.TRUTH[scenario]}
        # unnecessary = touching a service that isn't a true root, or repeating the same action on it
        unnecessary = sum(1 for i, a in enumerate(acts) if a[1] not in truth_svcs or a in acts[:i])
        if scenario == "0":  # control: success = nothing detected, nothing touched
            resolved = still_ok = int(not detected and not acts)
        else:
            resolved = int(inc.resolved)
            still_ok = 0
            if resolved:  # stays recovered after the agent walks away?
                time.sleep(STILL_OK_AFTER_S)
                still_ok = int(not stages.flags(adapter.get_health(), inc.baseline, strict=True))
        diag = int(chaos.diag_correct(scenario, roots)) if detected or scenario == "0" else 0
        row |= {
            "incident": inc.id, "detected": int(detected), "resolved": resolved, "still_ok": still_ok,
            "diag_correct": diag, "correct_and_recovered": int(diag and resolved and still_ok),
            "actions": len(acts), "unnecessary_actions": unnecessary,
            "actions_taken": ";".join(f"{a}({s})" for a, s in acts),
            "invariant_violations": "; ".join(getattr(inc, "violations", [])),
            "diagnosed": ";".join(f"{t}@{s}" for t, s in sorted(roots)) or "none",
            "mttd_s": round(inc.detected_at - inc.injected_at, 1) if inc.detected_at else "",
            "mttr_s": round(inc.recovered_at - inc.injected_at, 1) if inc.recovered_at else "",
            "iterations": inc.iteration, "escalations": inc.escalations, "tokens": tot["tokens"],
            "big_tokens": sum(r[3] + r[4] for r in tot["by_model"] if r[0] == "big"),
            "cost_usd": round(tot["cost"], 5),
        }
    except Exception as e:
        traceback.print_exc(file=log)
        row |= {"resolved": 0, "diag_correct": 0, "correct_and_recovered": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        gap = (time.time() - wall0) - (time.perf_counter() - mono0)
        row |= {"sleep_gap_s": round(gap, 1), "invalid": int(gap > SLEEP_GAP_LIMIT_S)}
        telemetry.console.file = None  # back to stdout
        log.close()
        shutil.rmtree(config.RUNBOOKS_DIR, ignore_errors=True)
    return row


def mean(xs, nd=1):
    xs = [float(x) for x in xs if x not in ("", None)]
    return (round(statistics.mean(xs), nd) if nd else round(statistics.mean(xs))) if xs else ""


def pct(xs):
    xs = [int(x) for x in xs]
    return f"{100 * sum(xs) / len(xs):.0f}%" if xs else ""


def report():
    with RESULTS.open() as f:
        allrows = list(csv.DictReader(f))
    rows = [r for r in allrows if r["invalid"] != "1"]
    invalid = len(allrows) - len(rows)
    order = {"hybrid": 0, "big-only": 1, "small-only": 2}
    faults = [r for r in rows if r["scenario"] != "0"]
    control = [r for r in rows if r["scenario"] == "0"]

    def stats(rs):
        return [len(rs), pct(r["diag_correct"] for r in rs), pct(r["resolved"] for r in rs),
                pct(r["correct_and_recovered"] for r in rs), mean(r["mttr_s"] for r in rs if r["resolved"] == "1"),
                mean((r["actions"] or 0 for r in rs), 2), mean((r["unnecessary_actions"] or 0 for r in rs), 2),
                mean((r["escalations"] or 0 for r in rs), 2), mean((r["cost_usd"] or 0 for r in rs), 4),
                round(sum(float(r["cost_usd"] or 0) for r in rs), 3)]

    def grouped(rs, key):
        g = {}
        for r in rs:
            g.setdefault(key(r), []).append(r)
        return sorted(g.items(), key=lambda kv: tuple(order.get(k, k) for k in kv[0]))

    head = ["runs", "diag accuracy", "recovered", "correct & recovered", "MTTR s", "actions/inc", "unnecessary/inc",
            "escalations", "cost/run $", "total $"]
    rollup = [[m, c, *stats(rs)] for (m, c), rs in grouped(faults, lambda r: (r["mode"], r["condition"]))]
    table = [[s_, m, c, *stats(rs), mean(r["mttd_s"] for r in rs)]
             for (s_, m, c), rs in grouped(rows, lambda r: (r["scenario"], r["mode"], r["condition"]))]
    cols = ["scenario", "mode", "condition", *head, "MTTD s"]
    ctrl = [[m, len(rs), sum(int(r["detected"] or 0) for r in rs), sum(int(r["actions"] or 0) for r in rs)]
            for (m,), rs in grouped(control, lambda r: (r["mode"],))]
    fails = [r for r in faults if r["mode"] == "small-only" and r["correct_and_recovered"] != "1"]
    viol = [r for r in rows if r["invariant_violations"]]
    errs = [r for r in rows if r["error"]]
    line = lambda r: "| " + " | ".join(map(str, r)) + " |"

    md = ["# Benchmark results", "",
          f"{len(rows)} valid runs ({invalid} discarded: machine slept > {SLEEP_GAP_LIMIT_S}s mid-run, rerun). "
          "Ground truth = `chaos.TRUTH` (scenario 6 is held out, graded on root service). MTTR averages resolved runs "
          f"only. **correct & recovered** = right diagnosis AND verified recovery AND still healthy {STILL_OK_AFTER_S}s "
          "later. **unnecessary** = an action on a service that is not a true root, or a repeated action. "
          "cold = no runbooks, warm = seed runbooks (none for scenarios 5 and 6).", "",
          "## Fault scenarios 1–6, by mode × condition", "",
          line(["mode", "condition", *head]), "|" + "---|" * (len(head) + 2), *map(line, rollup), "",
          "## Scenario 0: no-fault control", "",
          "| mode | runs | false detections | actions taken |", "|---|---|---|---|", *map(line, ctrl), "",
          "## By scenario", "", line(cols), "|" + "---|" * len(cols), *map(line, table), "",
          "## Where small-only fails (not correct & recovered)", ""]
    if fails:
        md += ["| run | diagnosed | truth | resolved | actions | error |", "|---|---|---|---|---|---|"]
        md += [f"| {r['key']} | {r['diagnosed']} | {';'.join(f'{t}@{s}' for t, s in sorted(chaos.TRUTH[r['scenario']]))} "
               f"| {r['resolved']} | {r['actions_taken']} | {r['error']} |" for r in fails]
    else:
        md.append("None.")
    md += ["", f"## Invariant violations: {len(viol)}", *[f"- {r['key']}: {r['invariant_violations']}" for r in viol],
           "", f"## Errors: {len(errs)}", *[f"- {r['key']}: {r['error']}" for r in errs]]
    (OUT / "results.md").write_text("\n".join(md) + "\n")
    with (OUT / "summary.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(table)
    print("\n".join(md))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--scenarios", default="0,1,2,3,4,5,6")
    ap.add_argument("--modes", default="hybrid,big-only,small-only")
    ap.add_argument("--conditions", default="cold,warm")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    config.TELEMETRY_DB = str(OUT / "telemetry.db")  # keep benchmark telemetry separate
    (OUT / "logs").mkdir(exist_ok=True)
    if not args.report_only:
        todo = [(s, m, c, i) for i in range(1, args.n + 1) for s in args.scenarios.split(",")
                for m in args.modes.split(",") for c in args.conditions.split(",")]
        skip = done_keys()
        todo = [t for t in todo if f"s{t[0]}-{t[1]}-{t[2]}-{t[3]}" not in skip]
        print(f"{len(todo)} runs to do ({len(skip)} already done)")
        for n, (s, m, c, i) in enumerate(todo, 1):
            t = time.time()
            for attempt in range(1, MAX_ATTEMPTS + 1):
                row = one(s, m, c, i)
                append(row)
                if not row["invalid"]:
                    break
                print(f"  {row['key']}: machine slept {row['sleep_gap_s']}s mid-run → invalid, rerunning "
                      f"({attempt}/{MAX_ATTEMPTS})", flush=True)
            print(f"[{n}/{len(todo)}] {row['key']}: resolved={row.get('resolved')} diag={row.get('diag_correct')} "
                  f"({row.get('diagnosed', '')}) mttr={row.get('mttr_s', '')} ${row.get('cost_usd', '')} "
                  f"{time.time() - t:.0f}s {row['error']}", flush=True)
        chaos.reset()
    report()
