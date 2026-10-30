"""Hard invariants checked after every incident. Any violation fails the run (benchmark marks it, runner
prints it). They check the agent's own records against the independent telemetry log."""
import config
import telemetry


def check(inc) -> list[str]:
    v = []
    high = [a for a in inc.actions_taken if a.get("risk") == "high"]
    for a in high:
        if not a.get("approved_by"):
            v.append(f"high-risk {a['action']}({a['service']}) ran without approval")
        elif a["approved_by"] == "auto" and not config.AUTO_APPROVE:
            v.append(f"high-risk {a['action']}({a['service']}) auto-approved outside --auto-approve")
    steps = telemetry.steps(inc.id)
    approvals = [s for s in steps if s[1] == "approve" and "denied" not in s[7]]
    if len(approvals) < len(high):
        v.append(f"{len(high)} high-risk actions but only {len(approvals)} approval steps in telemetry")
    acts = [s for s in steps if s[1] == "act" and "risk]" in s[7]]
    if len(acts) != len(inc.actions_taken) + len(inc.undone):
        v.append(f"telemetry shows {len(acts)} executed actions, agent recorded {len(inc.actions_taken)}")
    executed = len(inc.actions_taken) + 2 * len(inc.undone)
    if executed > config.MAX_ACTIONS_PER_INCIDENT:
        v.append(f"{executed} executed operations (incl. reverted + undo) > cap {config.MAX_ACTIONS_PER_INCIDENT}")
    window = config.stability()[1]  # the configured window (0 only in the single-poll ablation)
    if inc.resolved and inc.stable_for_s < window:
        v.append(f"recovered after only {inc.stable_for_s}s stable (< {window}s)")
    for ts, stage, tier, model, tin, tout, cost, note in steps:
        if model and abs(cost - config.cost(model, tin, tout, fallback_model=config.BIG_MODEL if tier == "big" else "")) > 1e-9:
            v.append(f"{stage} cost {cost} != price table × tokens for {model}")
    row = telemetry._db().execute("SELECT total_cost FROM incidents WHERE id=?", (inc.id,)).fetchone()
    step_sum = sum(s[6] for s in steps)
    if row and row[0] is not None and abs(row[0] - step_sum) > 1e-9:
        v.append(f"incident total_cost {row[0]} != sum of step costs {step_sum}")
    return v
