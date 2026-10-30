"""Compare benchmark result sets.

uv run python -m benchmarks.compare ablation          # benchmarks/ablation/<config>/results.csv → ablation/results.md
uv run python -m benchmarks.compare regress <dir>     # <dir>/results.csv vs the committed v2 benchmarks/results.csv;
                                                      # exit 1 on a regression
"""
import csv
import sys
from pathlib import Path

from benchmarks.run import mean, pct

BENCH = Path(__file__).parent
ABLATIONS = ["full", "guard", "compress", "fastpath", "verify"]
LABEL = {"full": "full hybrid", "guard": "(a) no topology guard", "compress": "(b) no log compression",
         "fastpath": "(c) no fast path", "verify": "(d) single-poll verify"}
# Regression thresholds per mode × condition cell, vs v2. Lenient: a quick run is n=1.
MAX_CR_DROP_PP = 20      # correct & recovered, percentage points
MAX_COST_RISE = 0.5      # +50% cost per run
MAX_MTTR_RISE = 0.3      # +30% MTTR


def rows(path: Path) -> list[dict]:
    with path.open() as f:
        return [r for r in csv.DictReader(f) if r["invalid"] != "1"]


def num(x) -> float:
    return float(x or 0)


def stats(rs: list[dict]) -> dict:
    res = [r for r in rs if r["resolved"] == "1"]
    return {"runs": len(rs), "cr": pct(r["correct_and_recovered"] for r in rs),
            "cr_n": sum(int(r["correct_and_recovered"]) for r in rs),
            "recovered": pct(r["resolved"] for r in rs),
            "false_rec": sum(1 for r in res if r["still_ok"] != "1"),
            "mttr": mean(r["mttr_s"] for r in res), "tokens": mean((r["tokens"] for r in rs), 0),
            "cost": mean((r["cost_usd"] or 0 for r in rs), 4), "esc": mean((r["escalations"] or 0 for r in rs), 2),
            "unnecessary": mean((r["unnecessary_actions"] or 0 for r in rs), 2),
            "viol": sum(1 for r in rs if r["invariant_violations"]), "err": sum(1 for r in rs if r["error"])}


def ablation():
    line = lambda xs: "| " + " | ".join(map(str, xs)) + " |"
    head = ["config", "runs", "correct & recovered", "recovered", "false recoveries", "MTTR s", "tokens / run",
            "cost / run $", "escalations", "unnecessary / inc", "errors"]
    out, per = [line(head), "|" + "---|" * len(head)], {}
    for a in ABLATIONS:
        f = BENCH / "ablation" / a / "results.csv"
        if not f.exists():
            continue
        rs = [r for r in rows(f) if r["scenario"] != "0"]
        s = stats(rs)
        out.append(line([LABEL[a], s["runs"], s["cr"], s["recovered"], s["false_rec"], s["mttr"], s["tokens"],
                         s["cost"], s["esc"], s["unnecessary"], s["err"]]))
        per[a] = {sc: stats([r for r in rs if r["scenario"] == sc]) for sc in sorted({r["scenario"] for r in rs})}
    scen = sorted({sc for p in per.values() for sc in p})
    by = [line(["config", *[f"s{sc}" for sc in scen]]), "|" + "---|" * (len(scen) + 1)]
    by += [line([LABEL[a], *[f"{p[sc]['cr_n']}/{p[sc]['runs']}" if sc in p else "" for sc in scen]]) for a, p in per.items()]
    md = ["# Ablation: hybrid, warm (seed runbooks), scenarios 1–6, n=2", "",
          "One component switched off at a time (`benchmarks/run.py --ablate`). **false recoveries** = the agent "
          "declared recovery but the system was not healthy 10s later.", "", *out, "",
          "## Correct & recovered by scenario", "", *by, ""]
    (BENCH / "ablation" / "results.md").write_text("\n".join(md))
    print("\n".join(md))


def regress(new_dir: str) -> int:
    new, base = rows(Path(new_dir) / "results.csv"), rows(BENCH / "results.csv")
    problems, table = [], []
    for key in sorted({(r["mode"], r["condition"]) for r in new}):
        n = [r for r in new if (r["mode"], r["condition"]) == key and r["scenario"] != "0"]
        scen = {r["scenario"] for r in n}
        b = [r for r in base if (r["mode"], r["condition"]) == key and r["scenario"] in scen]
        if not n or not b:
            continue
        sn, sb = stats(n), stats(b)
        crn, crb = 100 * sn["cr_n"] / sn["runs"], 100 * sb["cr_n"] / sb["runs"]
        table.append(f"{key[0]:>10} {key[1]:4}  correct&recovered {crb:5.0f}% → {crn:5.0f}%   "
                     f"MTTR {sb['mttr']} → {sn['mttr']}   cost/run {sb['cost']} → {sn['cost']}")
        if crb - crn > MAX_CR_DROP_PP:
            problems.append(f"{key}: correct & recovered dropped {crb:.0f}% → {crn:.0f}%")
        if sb["cost"] and num(sn["cost"]) > num(sb["cost"]) * (1 + MAX_COST_RISE):
            problems.append(f"{key}: cost/run rose {sb['cost']} → {sn['cost']}")
        if sb["mttr"] and sn["mttr"] and num(sn["mttr"]) > num(sb["mttr"]) * (1 + MAX_MTTR_RISE):
            problems.append(f"{key}: MTTR rose {sb['mttr']} → {sn['mttr']}")
    viol = [r["key"] for r in new if r["invariant_violations"]]
    false_alarms = [r["key"] for r in new if r["scenario"] == "0" and r["detected"] == "1"]
    problems += [f"invariant violations: {viol}"] * bool(viol) + [f"false alarms on the control: {false_alarms}"] * bool(false_alarms)
    print("vs committed v2 (benchmarks/results.csv), same scenarios only:")
    print("\n".join(table))
    print("\nREGRESSION:\n- " + "\n- ".join(problems) if problems else "\nno regression")
    return int(bool(problems))


if __name__ == "__main__":
    if sys.argv[1:2] == ["ablation"]:
        ablation()
    elif sys.argv[1:2] == ["regress"] and len(sys.argv) == 3:
        sys.exit(regress(sys.argv[2]))
    else:
        sys.exit(__doc__)
