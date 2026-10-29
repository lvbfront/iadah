"""Pipeline stages. Each takes (incident, adapter) and mutates the incident.
The adapter provides both Observability and Actions."""
import json
import re
import time
from pathlib import Path

import yaml
from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig
from rich.prompt import Confirm

import config
import telemetry
from agent import schemas as S
from agent.state import Incident
from llm.backends import get_backend, parse_json, validate
from router.rules import pick_tier

RUNBOOKS = Path("runbooks")
REPORTS = Path("reports")
OK_STATUS = ("healthy", "running")
ERR = re.compile(r"(?i)error|fatal|fail|exception|refused|timeout|denied|unavailable|503|not known")
DOCKER_TS = re.compile(r"^\S+Z\s+")


def est_tokens(text: str) -> int:
    return len(text) // 4  # ponytail: chars/4 estimate, swap for a real tokenizer if numbers get challenged


# ---------- helpers ----------

def compress_lines(lines: list[str], top: int = 12) -> list[str]:
    """drain3 template mining: identical-shaped lines collapse to one template with a count.
    Error-looking templates first, then by frequency."""
    cfg = TemplateMinerConfig()
    cfg.profiling_enabled = False
    cfg.masking_instructions = [
        MaskingInstruction(r"\d{4}-\d\d-\d\d[ T][\d:.,]+", "TS"),
        MaskingInstruction(r"\d+\.\d+\.\d+\.\d+(:\d+)?", "IP"),
        MaskingInstruction(r"(?<![A-Za-z])\d+(\.\d+)?(?![A-Za-z])", "NUM"),
    ]
    miner = TemplateMiner(config=cfg)
    for line in lines:
        if line := DOCKER_TS.sub("", line).strip():
            miner.add_log_message(line)
    clusters = sorted(miner.drain.clusters, key=lambda c: (not ERR.search(c.get_template()), -c.size))
    return [f"{c.size}x {c.get_template()[:300]}" for c in clusters[:top]]


def _plain(note: str) -> str:
    """Strip rich markup for the markdown report."""
    return re.sub(r"\[/?[^]]*\]", "", note).replace("|", "/")


def _since(inc: Incident) -> int:
    """Log window: from just before the fault, so earlier incidents don't pollute the evidence."""
    return int(time.time() - inc.injected_at) + 15 if inc.injected_at else 180


def _compact_health(health: dict) -> dict:
    return {s: {"status": h["status"], **({"app": h["app"]} if h.get("app") else {})} for s, h in health.items()}


def _route(inc: Incident, tier: str, reason: str):
    if tier == "big" and config.MODE == "hybrid":
        inc.escalations += 1
    inc.tier, inc.route_reason = tier, reason
    telemetry.log_step(inc.id, "route", note=f"→ [bold]{tier}[/]: {reason}")


def ask_json(inc: Incident, stage: str, tier: str, prompt: str, schema: dict, summarize, check=None):
    """Call the tier's model for schema-valid JSON. Retry once on invalid output, then escalate
    (router decides; in small-only mode there's nowhere to go). Returns (data|None, tier)."""
    while True:
        p = prompt
        for attempt in (1, 2):
            r = get_backend(tier).complete(p, S.SRE_SYSTEM, max_tokens=1500 if tier == "small" else 8000,
                                           schema=schema)
            data = parse_json(r.text)
            ok = data is not None and validate(data, schema) and (check is None or check(data))
            telemetry.log_step(inc.id, stage, r, note=summarize(data) if ok else
                               f"[red]invalid output (attempt {attempt})[/] {r.text[:80]!r}")
            if ok:
                return data, tier
            # Same prompt at temperature 0 would fail the same way: tell the model what went wrong.
            p = (prompt + f"\n\nYour previous reply was invalid:\n{r.text[:300]}\n"
                 f"Reply with one JSON object with exactly these keys filled in: {schema.get('required')}")
        new, reason = pick_tier(invalid_output=True)
        if new == tier:
            return None, tier
        _route(inc, new, f"{stage}: {reason}")
        tier = new


def _runbook(incident_type: str) -> tuple[Path, dict] | None:
    for p in sorted(RUNBOOKS.glob("*.yaml")):
        rb = yaml.safe_load(p.read_text())
        if rb.get("signature", {}).get("incident_type") == incident_type:
            return p, rb
    return None


# ---------- stages ----------

def detect(inc: Incident, obs, log: bool = True) -> bool:
    """Rules only. Unhealthy = container down/unhealthy, or app error rate / latency well above baseline."""
    t = time.perf_counter()
    inc.health = obs.get_health()
    bad = {}
    for s, h in inc.health.items():
        if h["status"] not in OK_STATUS + ("starting",):
            bad[s] = f"status={h['status']}"
        app, base = h.get("app") or {}, inc.baseline.get(s, {})
        if "error_rate" in app and app["error_rate"] > base.get("error_rate", 0) + 0.2:
            bad[s] = f"error_rate={app['error_rate']}"
        elif "avg_latency_ms" in app and base and app["avg_latency_ms"] > max(3 * base["avg_latency_ms"],
                                                                             base["avg_latency_ms"] + 200):
            bad[s] = f"latency={app['avg_latency_ms']}ms"
    inc.unhealthy = bad
    topo = obs.get_topology()
    # A service whose own dependencies are fine is a root-cause suspect; otherwise it's a symptom.
    inc.suspects = sorted(s for s in bad if not set(bad) & set(topo.get(s, [])))
    inc.symptoms = sorted(set(bad) - set(inc.suspects))
    if bad and log:
        telemetry.log_step(inc.id, "detect", latency_s=time.perf_counter() - t,
                           note=f"unhealthy={bad} → root suspects={inc.suspects} symptoms={inc.symptoms}")
    return bool(bad)


def compress_logs(inc: Incident, obs):
    t = time.perf_counter()
    raw = comp = n = 0
    topo = obs.get_topology()
    bad = set(inc.unhealthy)
    blast = {s for s, deps in topo.items() if bad & set(deps)}  # dependents see the fault first in their logs
    for svc in sorted(bad | blast):
        lines = obs.get_logs(svc, tail=500, since_s=_since(inc))
        inc.log_digest[svc] = compress_lines(lines)
        n += len(lines)
        raw += est_tokens("\n".join(lines))
        comp += est_tokens("\n".join(inc.log_digest[svc]))
    inc.raw_log_tokens, inc.compressed_log_tokens = raw, comp
    telemetry.update_incident(inc.id, raw_log_tokens=raw, compressed_log_tokens=comp)
    k = sum(map(len, inc.log_digest.values()))
    telemetry.log_step(inc.id, "compress", latency_s=time.perf_counter() - t,
                       note=f"{sorted(bad | blast)}: {n} log lines → {k} templates; ~{raw} → ~{comp} tokens "
                            f"([bold]{raw / max(comp, 1):.0f}x smaller[/])")


def triage(inc: Incident, obs):
    services = sorted(inc.health)
    prompt = S.TRIAGE_PROMPT.format(
        types=S.TYPES_TEXT, suspects=inc.suspects, symptoms=inc.symptoms,
        topology=json.dumps(obs.get_topology()), health=json.dumps(_compact_health(inc.health)),
        digest="\n".join(f"[{s}]\n" + "\n".join(d) for s, d in inc.log_digest.items()))
    tier = "big" if config.MODE == "big-only" else "small"
    data, _ = ask_json(inc, "triage", tier, prompt, S.triage_schema(services),
                       lambda d: f"type={d['incident_type']} root={d['root_cause_service']} "
                                 f"conf={d['confidence']:.2f} sev={d['severity']}",
                       check=lambda d: 0 <= d["confidence"] <= 1)
    inc.triage = data or {"incident_type": "unknown", "severity": "high", "affected_services": list(inc.unhealthy),
                          "suspected_root_cause": "triage failed", "confidence": 0.0,
                          "root_cause_service": (inc.suspects or services)[0], "evidence": []}


def route(inc: Incident, obs):
    t = inc.triage["incident_type"]
    tier, reason = pick_tier(confidence=inc.triage["confidence"], root_causes=max(1, len(inc.suspects)),
                             seen_before=t != "unknown" and _runbook(t) is not None, failed_fix=inc.failed_fix)
    _route(inc, tier, reason)


def investigate(inc: Incident, obs):
    services = sorted(inc.health)
    calls = set()

    def check(d):
        return d["tool"] in ("done", "get_health") or d["service"] in services

    for left in range(config.INVESTIGATE_BUDGET, 0, -1):
        prompt = S.INVESTIGATE_PROMPT.format(triage=json.dumps(inc.triage), left=left,
                                             evidence="\n".join(inc.evidence) or "(none beyond triage)")
        d, inc.tier = ask_json(inc, "investigate", inc.tier, prompt, S.investigate_schema(services),
                               lambda d: f"→ {d['tool']}({d['service']}): {d['reason'][:70]}", check)
        if d is None or d["tool"] == "done":
            break
        key = (d["tool"], d["service"])
        if key in calls:
            telemetry.log_step(inc.id, "investigate", note="repeated call, stopping")
            break
        calls.add(key)
        if d["tool"] == "get_logs":
            out = "\n".join(compress_lines(obs.get_logs(d["service"], tail=200, since_s=_since(inc)), top=8))
        elif d["tool"] == "get_metrics":
            out = json.dumps(obs.get_metrics(d["service"]))
        else:
            out = json.dumps(_compact_health(obs.get_health()))
        inc.evidence.append(f"{d['tool']}({d['service']}): {out[:1200]}")


def diagnose(inc: Incident, obs):
    services = sorted(inc.health)
    # Compact evidence pack: never raw logs.
    pack = json.dumps({"triage": inc.triage, "topology": obs.get_topology(),
                       "rule_suspects": inc.suspects, "rule_symptoms": inc.symptoms,
                       "health": _compact_health(inc.health), "compressed_logs": inc.log_digest,
                       "investigation": inc.evidence, "failed_attempts": inc.attempts})
    prompt = S.DIAGNOSE_PROMPT.format(types=S.TYPES_TEXT, pack=pack)

    def ok(d):
        return 0 <= d["confidence"] <= 1 and all(0 <= h["likelihood"] <= 1 for h in d["hypotheses"])

    d, inc.tier = ask_json(inc, "diagnose", inc.tier, prompt, S.diagnose_schema(services),
                           lambda d: f"type={d['incident_type']} root={d['root_cause_service']} "
                                     f"conf={d['confidence']:.2f} — {d['suspected_root_cause'][:80]} "
                                     f"[dim](pack ~{est_tokens(pack)} tok)[/]", ok)
    inc.diagnosis = d or inc.triage | {"hypotheses": []}
    # Guard: the fix target must actually be unhealthy; otherwise trust the topology suspect.
    root = inc.diagnosis["root_cause_service"]
    inc.root_service = root if root in inc.unhealthy or not inc.suspects else inc.suspects[0]
    if inc.root_service != root:
        telemetry.log_step(inc.id, "diagnose", note=f"root {root} is healthy → using topology suspect {inc.root_service}")


def plan(inc: Incident, obs):
    t = inc.diagnosis["incident_type"]
    tried = [(a["action"], a["service"]) for att in inc.attempts for a in att["actions"]]
    hit = _runbook(t) if t != "unknown" else None
    if hit:
        path, rb = hit
        steps = [{"action": a["action"], "service": inc.root_service if a["service"] == "$root" else a["service"],
                  "reason": f"runbook {rb['name']}"} for a in rb["actions"]]
        if not any((s["action"], s["service"]) in tried for s in steps):
            inc.plan, inc.plan_source = steps, f"runbook:{path.stem}"
            telemetry.log_step(inc.id, "plan", note=f"runbook hit [bold]{path.name}[/] → "
                                                    + ", ".join(f"{s['action']}({s['service']})" for s in steps))
            return
        telemetry.log_step(inc.id, "plan", note=f"runbook {path.name} already tried → asking planner model")
    else:
        telemetry.log_step(inc.id, "plan", note=f"no runbook for {t} → asking planner model")
    services = sorted(inc.health)
    allow = "\n".join(f"- {a} (risk {r}): {desc}" for a, (r, desc) in S.ALLOWLIST.items())
    prompt = S.PLAN_PROMPT.format(allowlist=allow, diagnosis=json.dumps(inc.diagnosis), tried=tried,
                                  topology=json.dumps(obs.get_topology()))
    d, inc.tier = ask_json(inc, "plan", inc.tier, prompt, S.plan_schema(services),
                           lambda d: ", ".join(f"{a['action']}({a['service']})" for a in d["actions"]),
                           check=lambda d: len(d["actions"]) > 0)
    inc.plan, inc.plan_source = (d["actions"] if d else []), "model"


def act(inc: Incident, obs):
    done = []
    for step in inc.plan:
        a, svc = step["action"], step["service"]
        if a not in S.ALLOWLIST:
            telemetry.log_step(inc.id, "act", note=f"[red]blocked[/] {a}({svc}): not allowlisted")
            continue
        risk = S.ALLOWLIST[a][0]
        if risk == "high":
            if config.AUTO_APPROVE:
                telemetry.log_step(inc.id, "approve", tier="human", note=f"{a}({svc}) risk=high → auto-approved (--auto-approve)")
            else:
                t = time.perf_counter()
                yes = Confirm.ask(f"[yellow]Approve HIGH-risk action[/] {a}({svc})? reason: {step['reason']}",
                                  console=telemetry.console)
                telemetry.log_step(inc.id, "approve", tier="human", latency_s=time.perf_counter() - t,
                                   note=f"{a}({svc}) → {'approved' if yes else '[red]denied[/]'}")
                if not yes:
                    continue
        t = time.perf_counter()
        try:
            rec = getattr(obs, a)(svc)
        except Exception as e:
            telemetry.log_step(inc.id, "act", latency_s=time.perf_counter() - t, note=f"[red]{a}({svc}) failed: {e}[/]")
            for prev in reversed(done):  # all-or-nothing: undo this plan's earlier actions
                telemetry.log_step(inc.id, "undo", note=obs.undo(prev)["detail"])
            done = []
            break
        rec["risk"] = risk
        done.append(rec)
        telemetry.log_step(inc.id, "act", latency_s=time.perf_counter() - t, note=f"[{risk} risk] {rec['detail']}")
    inc.actions_taken += done
    inc.plan = done


def verify(inc: Incident, obs):
    """Recovered = every service healthy AND app error rate / latency back near the pre-fault baseline."""
    t0 = time.perf_counter()
    report = {}
    while time.perf_counter() - t0 < config.VERIFY_TIMEOUT_S:
        health = obs.get_health()
        report = {}
        for s, h in health.items():
            if h["status"] not in OK_STATUS:
                report[s] = f"status={h['status']}"
            app, base = h.get("app") or {}, inc.baseline.get(s, {})
            if base and "error_rate" not in app:
                report[s] = f"metrics unavailable: {app.get('error', '?')}"
            elif base and app["error_rate"] > base["error_rate"] + 0.05:
                report[s] = f"error_rate={app['error_rate']} (baseline {base['error_rate']})"
            elif base and app["avg_latency_ms"] > max(3 * base["avg_latency_ms"], base["avg_latency_ms"] + 50):
                report[s] = f"latency={app['avg_latency_ms']}ms (baseline {base['avg_latency_ms']}ms)"
        if not report:
            break
        time.sleep(2)
    inc.resolved = not report
    inc.failed_fix = not inc.resolved
    if inc.resolved:
        inc.recovered_at = time.time()
        for s in health:
            obs.mark_known_good(s)  # verified-healthy config becomes the new rollback target
        base = {s: h["app"] for s, h in health.items() if h.get("app")}
        telemetry.log_step(inc.id, "verify", latency_s=time.perf_counter() - t0,
                           note=f"[green]recovered[/]: all healthy, metrics at baseline {base}")
    else:
        inc.attempts.append({"iteration": inc.iteration, "actions": inc.plan, "verify": report})
        inc.evidence.append(f"verify after {[a['action'] + '(' + a['service'] + ')' for a in inc.plan]}: still failing {report}")
        telemetry.log_step(inc.id, "verify", latency_s=time.perf_counter() - t0, note=f"[red]not recovered[/]: {report}")


def postmortem(inc: Incident, obs):
    d = inc.diagnosis or inc.triage
    t = d.get("incident_type", "unknown")
    # Save/update the runbook so the next occurrence is handled without escalation.
    if inc.resolved and t != "unknown" and inc.plan:
        hit = _runbook(t)
        if inc.plan_source.startswith("runbook:") and hit:
            path, rb = hit
            rb["times_used"] = rb.get("times_used", 0) + 1
            rb["last_used"] = inc.id
            inc.runbook_saved = f"{path.name} (times_used={rb['times_used']})"
        else:
            path = hit[0] if hit else RUNBOOKS / f"{t}.yaml"
            rb = {"name": t.replace("_", "-"), "signature": {"incident_type": t},
                  "description": d.get("suspected_root_cause", ""),
                  "actions": [{"action": a["action"], "service": "$root" if a["service"] == inc.root_service
                               else a["service"]} for a in inc.plan],
                  "source": f"learned from {inc.id}", "times_used": 1}
            inc.runbook_saved = f"{path.name} ({'updated' if hit else 'new'})"
        path.write_text(yaml.safe_dump(rb, sort_keys=False))

    tot = telemetry.totals(inc.id)
    mttd = inc.detected_at - inc.injected_at if inc.detected_at else None
    mttr = inc.recovered_at - inc.injected_at if inc.recovered_at else None
    telemetry.update_incident(inc.id, detected_at=inc.detected_at, recovered_at=inc.recovered_at or None,
                              mttd_s=mttd, mttr_s=mttr, resolved=int(inc.resolved), iterations=inc.iteration,
                              escalations=inc.escalations, total_cost=tot["cost"], total_tokens=tot["tokens"])
    fmt = lambda x: f"{x:.1f}s" if x is not None else "n/a"
    rows = telemetry.steps(inc.id)
    t0 = inc.injected_at or rows[0][0]
    lines = [
        f"# Incident report {inc.id}", "",
        f"- **Scenario:** {inc.scenario or 'live'}   **Mode:** {config.MODE}",
        f"- **Resolved:** {'yes' if inc.resolved else 'NO'} after {inc.iteration} iteration(s)",
        f"- **MTTD:** {fmt(mttd)}   **MTTR:** {fmt(mttr)}",
        f"- **Escalations to big model:** {inc.escalations}",
        f"- **Cost:** ${tot['cost']:.5f}   **LLM tokens:** {tot['tokens']}",
        f"- **Log compression:** ~{inc.raw_log_tokens} → ~{inc.compressed_log_tokens} tokens", "",
        "## Root cause",
        f"- **Type:** `{t}`   **Service:** `{inc.root_service}`   **Confidence:** {d.get('confidence', 0):.2f}",
        f"- {d.get('suspected_root_cause', '')}",
        *[f"  - hypothesis ({h['likelihood']:.2f}) `{h['service']}`: {h['cause']}" for h in d.get("hypotheses", [])],
        f"- Symptoms (explained by topology): {inc.symptoms or 'none'}", "",
        "## Actions",
        *[f"- `{a['action']}({a['service']})` [{a.get('risk', '?')} risk]: {a['detail']}" for a in inc.actions_taken],
        f"- Plan source: {inc.plan_source}", "",
        "## Models used",
        "| tier | model | calls | in tok | out tok | latency | cost |", "|---|---|---|---|---|---|---|",
        *[f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]:.1f}s | ${r[6]:.5f} |" for r in tot["by_model"]], "",
        "## Timeline",
        "| t | stage | tier | model | tokens | cost | note |", "|---|---|---|---|---|---|---|",
        *[f"| {ts - t0:.1f}s | {st} | {tier} | {m} | {i}→{o} | ${c:.5f} | {_plain(n)} |"
          for ts, st, tier, m, i, o, c, n in rows], "",
        f"## Runbook\n{inc.runbook_saved or 'not saved (unresolved or unknown type)'}",
    ]
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{inc.id}.md"
    out.write_text("\n".join(lines) + "\n")
    telemetry.log_step(inc.id, "postmortem", note=f"report → {out}; runbook → {inc.runbook_saved or 'none'}")
