"""Pipeline stages. Each takes (incident, adapter) and mutates the incident.
The adapter provides both Observability and Actions."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
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

REPORTS = Path("reports")
OK_STATUS = ("healthy", "running")
MEM_ALERT_PCT = 75
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
    return {s: {"status": h["status"], "mem_pct": h.get("mem_pct"), **({"app": h["app"]} if h.get("app") else {})}
            for s, h in health.items()}


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


def signature(d: dict) -> list[str]:
    """Incident signature = sorted set of root-cause types (a combo of faults is its own signature)."""
    types = {rc["incident_type"] for rc in d.get("root_causes", [])} - {"unknown"}
    return sorted(types or {d["incident_type"]})


def _runbook(sig: list[str]) -> tuple[Path, dict] | None:
    if sig == ["unknown"]:
        return None
    for p in sorted(Path(config.RUNBOOKS_DIR).glob("*.yaml")):
        rb = yaml.safe_load(p.read_text())
        if sorted(rb.get("signature", {}).get("incident_types", [])) == sig:
            return p, rb
    return None


def _known(inc: Incident, d: dict) -> bool:
    """A runbook exists for this signature AND it explains every topology suspect."""
    sig = signature(d)
    return _runbook(sig) is not None and len(sig) >= len(inc.suspects)


def depth(topo: dict, svc: str, seen=()) -> int:
    """0 for leaf dependencies; fixes run in ascending depth (dependencies before dependents)."""
    deps = [d for d in topo.get(svc, []) if d not in seen]
    return 1 + max(depth(topo, d, seen + (svc,)) for d in deps) if deps else 0


def flags(health: dict, baseline: dict, strict: bool = False) -> dict:
    """Shared health rules. detect uses loose thresholds; verify (strict) uses tight ones and also
    fails on 'starting' and on missing metrics for services that had a baseline."""
    err_tol, lat_add = (0.05, 50) if strict else (0.2, 200)
    bad = {}
    for s, h in health.items():
        if h["status"] not in OK_STATUS + (() if strict else ("starting",)):
            bad[s] = f"status={h['status']}"
        if (h.get("mem_pct") or 0) > MEM_ALERT_PCT:
            bad[s] = f"memory={h['mem_pct']}% of limit"
        app, base = h.get("app") or {}, baseline.get(s, {})
        if strict and base and "error_rate" not in app:
            bad[s] = f"metrics unavailable: {app.get('error', '?')}"
        elif "error_rate" in app and app["error_rate"] > base.get("error_rate", 0) + err_tol:
            bad[s] = f"error_rate={app['error_rate']} (baseline {base.get('error_rate', 0)})"
        elif base and "avg_latency_ms" in app and app["avg_latency_ms"] > max(3 * base["avg_latency_ms"],
                                                                              base["avg_latency_ms"] + lat_add):
            bad[s] = f"latency={app['avg_latency_ms']}ms (baseline {base['avg_latency_ms']}ms)"
    return bad


def fault_checks(inc: Incident, obs, health: dict, mem0: dict) -> dict:
    """Checks specific to each diagnosed root cause, on top of the generic health rules.
    mem0 holds memory readings from the start of the current healthy streak."""
    bad = {}
    for r in inc.roots:
        t, svc = r["incident_type"], r["service"]
        key = f"{svc}:{t}"
        try:
            if t == "db_pool_exhaustion":
                u = obs.get_metrics(svc).get("conn_usage_pct")
                if u is None or u > 70:
                    bad[key] = f"connection slots in use {u}% (need ≤ 70%)"
            elif t == "memory_leak":
                m = (health.get(svc) or {}).get("mem_pct") or 0
                first = mem0.setdefault(svc, m)
                if m - first > 10:
                    bad[key] = f"memory still growing {first}% → {m}%"
            elif t == "disk_full":
                d = obs.get_metrics(svc).get("disk", {}).get("used_pct")
                if d is None or d > 80:
                    bad[key] = f"scratch disk {d}% used (need ≤ 80%)"
            elif (health.get(svc) or {}).get("status") not in OK_STATUS:
                bad[key] = f"{svc} status {(health.get(svc) or {}).get('status')}"
        except Exception as e:
            bad[key] = f"check failed: {e}"
    return bad


# ---------- stages ----------

def detect(inc: Incident, obs, log: bool = True, merge: bool = False) -> bool:
    """Rules only. Unhealthy = container down/unhealthy, or app error rate / latency well above baseline.
    merge=True unions with the previous snapshot (alert grouping: health checks can flap)."""
    t = time.perf_counter()
    inc.health = obs.get_health()
    bad = flags(inc.health, inc.baseline)
    inc.unhealthy = (inc.unhealthy | bad) if merge else bad
    bad = inc.unhealthy
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


def gather(inc: Incident, obs):
    """Evidence that's obviously needed is fetched in code, in parallel: metrics of every unhealthy
    service, each root suspect, and the suspects' dependencies. No LLM tool calls."""
    t = time.perf_counter()
    topo = obs.get_topology()
    targets = sorted(set(inc.unhealthy) | {d for s in inc.suspects for d in topo.get(s, [])})
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(zip(targets, ex.map(_safe_metrics(obs), targets)))
    inc.metrics |= results
    inc.evidence.append("metrics (gathered in code): " + json.dumps(results)[:2500])
    telemetry.log_step(inc.id, "gather", latency_s=time.perf_counter() - t,
                       note=f"{len(targets)} parallel metric reads {targets} in code")


def _safe_metrics(obs):
    def f(svc):
        try:
            m = obs.get_metrics(svc)
            m.get("app", {}).pop("started", None)
            m.get("app", {}).pop("latency_ms_sum", None)
            return m
        except Exception as e:
            return {"error": str(e)}
    return f


def is_ambiguous(inc: Incident) -> bool:
    return (inc.failed_fix or inc.triage["incident_type"] == "unknown"
            or inc.triage["confidence"] < config.FAST_PATH_CONFIDENCE)


def fast_path(inc: Incident) -> bool:
    """High-confidence triage of a known signature: skip investigate + diagnose."""
    if inc.iteration > 1 or is_ambiguous(inc) or not _known(inc, inc.triage):
        return False
    inc.fast_path = True
    inc.diagnosis = inc.triage | {"hypotheses": []}
    _resolve_roots(inc)
    telemetry.log_step(inc.id, "fast-path", note=f"[bold green]fast path[/]: conf {inc.triage['confidence']:.2f} ≥ "
                                                 f"{config.FAST_PATH_CONFIDENCE} + runbook for {signature(inc.triage)}"
                                                 " → skip investigate/diagnose")
    return True


def _resolve_roots(inc: Incident):
    """Guard: each fix target must actually be unhealthy; otherwise use an unclaimed topology suspect."""
    d = inc.diagnosis
    rcs = [rc for rc in d.get("root_causes", []) if rc["incident_type"] != "unknown"] or \
          [{"incident_type": d["incident_type"], "service": d["root_cause_service"]}]
    roots, claimed = [], set()
    for rc in rcs:
        svc = rc["service"]
        if svc not in inc.unhealthy:
            free = [s for s in inc.suspects if s not in claimed and s not in {r["service"] for r in rcs}]
            if free:
                telemetry.log_step(inc.id, "diagnose", note=f"root {svc} is healthy → using topology suspect {free[0]}")
                svc = free[0]
        claimed.add(svc)
        roots.append({"incident_type": rc["incident_type"], "service": svc})
    inc.roots = roots
    inc.root_service = roots[0]["service"]


def _drop_symptom_roots(inc: Incident, d: dict | None):
    """A service failing only because a dependency failed is a symptom, never a root cause."""
    if d and inc.symptoms:
        keep = [rc for rc in d.get("root_causes", []) if rc["service"] not in inc.symptoms]
        if len(keep) < len(d.get("root_causes", [])):
            telemetry.log_step(inc.id, "route", note=f"dropped symptom roots on {inc.symptoms} (topology)")
            d["root_causes"] = keep
    return d


def triage(inc: Incident, obs):
    services = sorted(inc.health)
    prompt = S.TRIAGE_PROMPT.format(
        types=S.TYPES_TEXT, suspects=inc.suspects, symptoms=inc.symptoms, alerts=json.dumps(inc.unhealthy),
        topology=json.dumps(obs.get_topology()), health=json.dumps(_compact_health(inc.health)),
        metrics=json.dumps(inc.metrics),
        digest="\n".join(f"[{s}]\n" + "\n".join(d) for s, d in inc.log_digest.items()))
    tier = "big" if config.MODE == "big-only" else "small"
    data, _ = ask_json(inc, "triage", tier, prompt, S.triage_schema(services),
                       lambda d: f"roots={[(r['incident_type'], r['service']) for r in d['root_causes']]} "
                                 f"conf={d['confidence']:.2f} sev={d['severity']}",
                       check=lambda d: 0 <= d["confidence"] <= 1)
    inc.triage = _drop_symptom_roots(inc, data) or {"incident_type": "unknown", "severity": "high", "affected_services": list(inc.unhealthy),
                          "suspected_root_cause": "triage failed", "confidence": 0.0,
                          "root_cause_service": (inc.suspects or services)[0], "root_causes": [], "evidence": []}


def route(inc: Incident, obs):
    tier, reason = pick_tier(confidence=inc.triage["confidence"],
                             root_causes=max(1, len(inc.suspects), len(signature(inc.triage))),
                             seen_before=_known(inc, inc.triage), failed_fix=inc.failed_fix)
    _route(inc, tier, reason)


def investigate(inc: Incident, obs):
    """LLM-chosen tool loop, only for ambiguous cases; obvious evidence was gathered in code."""
    if not is_ambiguous(inc):
        telemetry.log_step(inc.id, "investigate", note=f"skipped: not ambiguous (conf {inc.triage['confidence']:.2f}), "
                                                       "code-gathered evidence suffices")
        return
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
    pack = json.dumps({"alerts": inc.unhealthy, "triage": inc.triage, "topology": obs.get_topology(),
                       "rule_suspects": inc.suspects, "rule_symptoms": inc.symptoms,
                       "health": _compact_health(inc.health), "compressed_logs": inc.log_digest,
                       "investigation": inc.evidence, "failed_attempts": inc.attempts})
    prompt = S.DIAGNOSE_PROMPT.format(types=S.TYPES_TEXT, pack=pack)

    def ok(d):
        return 0 <= d["confidence"] <= 1 and all(0 <= h["likelihood"] <= 1 for h in d["hypotheses"])

    d, inc.tier = ask_json(inc, "diagnose", inc.tier, prompt, S.diagnose_schema(services),
                           lambda d: f"roots={[(r['incident_type'], r['service']) for r in d['root_causes']]} "
                                     f"conf={d['confidence']:.2f} — {d['suspected_root_cause'][:80]} "
                                     f"[dim](pack ~{est_tokens(pack)} tok)[/]", ok)
    inc.diagnosis = _drop_symptom_roots(inc, d) or inc.triage | {"hypotheses": []}
    _resolve_roots(inc)


def plan(inc: Incident, obs):
    sig = signature(inc.diagnosis)
    topo = obs.get_topology()
    tried = [(a["action"], a["service"]) for att in inc.attempts for a in att["actions"]]
    by_type = {r["incident_type"]: r["service"] for r in inc.roots}
    hit = _runbook(sig)
    steps = []
    if hit:
        path, rb = hit
        for a in rb["actions"]:
            svc = a["service"]
            if svc.startswith("$root"):  # "$root:<incident_type>" (or "$root" = primary root)
                svc = by_type.get(svc.partition(":")[2], inc.root_service)
            steps.append({k: v for k, v in a.items() if k not in ("action", "service")}
                         | {"action": a["action"], "service": svc, "reason": f"runbook {rb['name']}"})
        # Steps already tried on services that are healthy now worked: drop them. A tried step on a
        # still-broken service means the runbook doesn't fix this → planner.
        done = [x for x in steps if (x["action"], x["service"]) in tried and x["service"] not in inc.unhealthy]
        steps = [x for x in steps if x not in done]
        if done:
            telemetry.log_step(inc.id, "plan", note="already fixed: " + ", ".join(f"{x['action']}({x['service']})" for x in done))
        if not steps or any((x["action"], x["service"]) in tried for x in steps):
            telemetry.log_step(inc.id, "plan", note=f"runbook {path.name} exhausted → asking planner model")
            steps = []
        else:
            inc.plan_source = f"runbook:{path.stem}"
    else:
        telemetry.log_step(inc.id, "plan", note=f"no runbook for {sig} → asking planner model")
    if not steps:
        services = sorted(inc.health)
        allow = "\n".join(f"- {a} (risk {r}): {desc}" for a, (r, desc) in S.ALLOWLIST.items())
        prompt = S.PLAN_PROMPT.format(allowlist=allow, diagnosis=json.dumps(inc.diagnosis | {"resolved_roots": inc.roots}),
                                      tried=tried, topology=json.dumps(topo))
        d, inc.tier = ask_json(inc, "plan", inc.tier, prompt, S.plan_schema(services),
                               lambda d: "proposed " + ", ".join(f"{a['action']}({a['service']})" for a in d["actions"]),
                               check=lambda d: len(d["actions"]) > 0)
        steps, inc.plan_source = (d["actions"] if d else []), "model"
    # Dependency order: fix lower-level services (databases, caches) before the services that use them.
    inc.plan = sorted(steps, key=lambda x: depth(topo, x["service"]))
    if inc.plan:
        telemetry.log_step(inc.id, "plan", note=f"{inc.plan_source} → dependency order: "
                           + " → ".join(f"{x['action']}({x['service']})[d{depth(topo, x['service'])}]" for x in inc.plan))


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
            rec = getattr(obs, a)(svc, **({"replicas": int(step.get("replicas", 2))} if a == "scale" else {}))
        except Exception as e:
            telemetry.log_step(inc.id, "act", latency_s=time.perf_counter() - t, note=f"[red]{a}({svc}) failed: {e}[/]")
            for prev in reversed(done):  # all-or-nothing: undo this plan's earlier actions
                telemetry.log_step(inc.id, "undo", note=obs.undo(prev)["detail"])
            done = []
            break
        rec["risk"] = risk
        done.append(rec)
        telemetry.log_step(inc.id, "act", latency_s=time.perf_counter() - t, note=f"[{risk} risk] {rec['detail']}")
        if not _wait_service(inc, obs, svc):
            break  # a dependency that didn't come back makes fixes above it pointless
    inc.actions_taken += done
    inc.plan = done


def _wait_service(inc: Incident, obs, svc: str, timeout: float = 30) -> bool:
    """Per-fix verify: the fixed service itself is healthy before moving up the dependency graph."""
    t = time.perf_counter()
    while time.perf_counter() - t < timeout:
        st = obs.get_health().get(svc, {}).get("status")
        if st in OK_STATUS:
            telemetry.log_step(inc.id, "verify-fix", latency_s=time.perf_counter() - t, note=f"{svc} is {st}")
            return True
        time.sleep(1)
    telemetry.log_step(inc.id, "verify-fix", latency_s=time.perf_counter() - t, note=f"[red]{svc} still {st}[/]")
    return False


def verify(inc: Incident, obs):
    """Recovered = STABLE_POLLS consecutive polls spanning ≥ STABILIZE_S where every service passes the
    strict health rules (status, memory, error rate and latency near the pre-fault baseline) AND every
    diagnosed root cause passes its fault-specific check. Any failing poll restarts the streak."""
    t0 = time.perf_counter()
    report, streak, since, mem0 = {}, 0, None, {}
    while time.perf_counter() - t0 < config.VERIFY_TIMEOUT_S:
        health = obs.get_health()
        report = flags(health, inc.baseline, strict=True) or fault_checks(inc, obs, health, mem0)
        if report:
            streak, since, mem0 = 0, None, {}
        else:
            streak += 1
            since = since or time.time()
            if streak >= config.STABLE_POLLS and time.time() - since >= config.STABILIZE_S:
                break
        time.sleep(config.VERIFY_POLL_S)
    inc.stable_for_s = round(time.time() - since, 1) if since else 0.0
    inc.resolved = (not report and streak >= config.STABLE_POLLS and inc.stable_for_s >= config.STABILIZE_S)
    if not report and not inc.resolved:
        report = {"*": f"healthy for only {inc.stable_for_s}s / {streak} polls before timeout"}
    inc.failed_fix = not inc.resolved
    if inc.resolved:
        inc.recovered_at = time.time()
        for s in health:
            obs.mark_known_good(s)  # verified-healthy config becomes the new rollback target
        base = {s: h["app"] for s, h in health.items() if h.get("app")}
        telemetry.log_step(inc.id, "verify", latency_s=time.perf_counter() - t0,
                           note=f"[green]recovered[/]: {streak} consecutive healthy polls over {inc.stable_for_s}s, "
                                f"fault checks pass for {[r['incident_type'] + '@' + r['service'] for r in inc.roots]}; {base}")
    else:
        inc.attempts.append({"iteration": inc.iteration, "actions": inc.plan, "verify": report})
        inc.evidence.append(f"verify after {[a['action'] + '(' + a['service'] + ')' for a in inc.plan]}: still failing {report}")
        telemetry.log_step(inc.id, "verify", latency_s=time.perf_counter() - t0, note=f"[red]not recovered[/]: {report}")


def postmortem(inc: Incident, obs):
    d = inc.diagnosis or inc.triage
    sig = signature(d) if d else ["unknown"]
    t = "+".join(sig)
    # Save/update the runbook so the next occurrence is handled without escalation.
    # Learn from every action that ran across all iterations, not just the last one.
    learned = list({(a["action"], a["service"]): a for a in inc.actions_taken}.values())
    if inc.resolved and sig != ["unknown"] and learned:
        hit = _runbook(sig)
        if inc.plan_source.startswith("runbook:") and hit:
            path, rb = hit
            rb["times_used"] = rb.get("times_used", 0) + 1
            rb["last_used"] = inc.id
            inc.runbook_saved = f"{path.name} (times_used={rb['times_used']})"
        else:
            path = hit[0] if hit else Path(config.RUNBOOKS_DIR) / f"{t}.yaml"
            svc_type = {r["service"]: r["incident_type"] for r in inc.roots}
            rb = {"name": t.replace("_", "-"), "signature": {"incident_types": sig},
                  "description": d.get("suspected_root_cause", ""),
                  "actions": [{k: v for k, v in a.items() if k in ("action", "replicas")}
                              | {"service": f"$root:{svc_type[a['service']]}" if a["service"] in svc_type
                                 else a["service"]} for a in learned],
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
        f"- **Signature:** `{t}`   **Confidence:** {d.get('confidence', 0):.2f}",
        *[f"- Root cause `{r['incident_type']}` on `{r['service']}`" for r in inc.roots],
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
