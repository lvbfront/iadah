"""Pipeline stages. Each takes (incident, adapter) and mutates the incident.
detect is real (rules only); the rest are stubs for now."""
import time

import telemetry
from agent.state import Incident


def detect(inc: Incident, obs) -> None:
    t = time.perf_counter()
    health = obs.get_health()
    inc.unhealthy = {s: h for s, h in health.items() if h["status"] not in ("healthy", "running")}
    # Apps can be "healthy" at the container level but erroring; the api health endpoint covers that.
    topo = obs.get_topology()
    bad = set(inc.unhealthy)
    # A service whose own dependencies are fine is a root-cause suspect; otherwise it's a symptom.
    inc.suspects = sorted(s for s in bad if not bad & set(topo.get(s, [])))
    inc.symptoms = sorted(bad - set(inc.suspects))
    telemetry.log_step(inc.id, "detect", latency_s=time.perf_counter() - t,
                       note=f"unhealthy={sorted(bad)} suspects={inc.suspects}")


def compress_logs(inc, obs):
    # TODO: drain3 template mining + dedupe over obs.get_logs(svc) for suspects + symptoms
    telemetry.log_step(inc.id, "compress", note="stub")


def triage(inc, obs):
    # TODO: small model classifies incident type + confidence from log_digest
    telemetry.log_step(inc.id, "triage", note="stub")


def investigate(inc, obs):
    # TODO: tool loop over obs.get_logs/get_metrics
    telemetry.log_step(inc.id, "investigate", note="stub")


def diagnose(inc, obs):
    # TODO: router.pick_tier(...) -> llm.get_backend(tier)
    telemetry.log_step(inc.id, "diagnose", note="stub")


def plan(inc, obs):
    # TODO: runbooks/ lookup first, LLM only if no match
    telemetry.log_step(inc.id, "plan", note="stub")


def act(inc, obs):
    # TODO: allowlist; low-risk auto, high-risk asks human; record for rollback
    telemetry.log_step(inc.id, "act", note="stub")


def verify(inc, obs):
    # TODO: also compare metrics to baseline
    inc.resolved = not any(h["status"] not in ("healthy", "running") for h in obs.get_health().values())
    inc.failed_fix = not inc.resolved
    telemetry.log_step(inc.id, "verify", note=f"resolved={inc.resolved}")


def postmortem(inc, obs):
    # TODO: write runbooks/<type>.md for reuse
    telemetry.log_step(inc.id, "postmortem", note="stub")
