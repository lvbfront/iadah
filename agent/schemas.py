"""JSON schemas + prompts for the LLM stages. Schemas are built per incident so the
service enum matches the live topology."""

INCIDENT_TYPES = {
    "cache_down": "cache (Redis) unreachable or stopped",
    "db_auth_failure": "DB rejects a client's credentials (often after a config deploy); DB itself is healthy",
    "db_pool_exhaustion": "app can't get DB connections from its pool; DB itself is healthy",
    "memory_leak": "a service's memory grows until it is OOM-killed or restarts",
    "disk_full": "a volume is full; writes fail",
    "slow_queries": "DB queries are slow; latency high, errors low",
    "unknown": "none of the above",
}

# risk tier per allowlisted action; only these may ever be executed
ALLOWLIST = {
    "restart": ("low", "restart the service's container"),
    "rollback_config": ("high", "restore the service's last-known-good config file and restart it"),
}


def triage_schema(services: list[str]) -> dict:
    return {"type": "object",
            "required": ["incident_type", "severity", "affected_services", "suspected_root_cause",
                         "root_cause_service", "confidence", "evidence"],
            "properties": {
                "incident_type": {"type": "string", "enum": list(INCIDENT_TYPES)},
                "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "affected_services": {"type": "array", "items": {"type": "string", "enum": services}},
                "suspected_root_cause": {"type": "string"},
                "root_cause_service": {"type": "string", "enum": services},
                "confidence": {"type": "number"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            }}


def diagnose_schema(services: list[str]) -> dict:
    s = triage_schema(services)
    s["required"] = s["required"] + ["hypotheses"]
    s["properties"] = s["properties"] | {"hypotheses": {"type": "array", "items": {
        "type": "object", "required": ["cause", "service", "likelihood"],
        "properties": {"cause": {"type": "string"}, "service": {"type": "string", "enum": services},
                       "likelihood": {"type": "number"}}}}}
    return s


def investigate_schema(services: list[str]) -> dict:
    return {"type": "object", "required": ["tool", "service", "reason"], "properties": {
        "tool": {"type": "string", "enum": ["get_logs", "get_metrics", "get_health", "done"]},
        "service": {"type": "string", "enum": services + ["none"]},
        "reason": {"type": "string"}}}


def plan_schema(services: list[str]) -> dict:
    return {"type": "object", "required": ["actions"], "properties": {"actions": {"type": "array", "items": {
        "type": "object", "required": ["action", "service", "reason"],
        "properties": {"action": {"type": "string", "enum": list(ALLOWLIST)},
                       "service": {"type": "string", "enum": services},
                       "reason": {"type": "string"}}}}}}


SRE_SYSTEM = ("You are an SRE agent recovering a failing production system. Be precise and terse. "
              "Base every claim on the evidence given. Output only the requested JSON.")

TYPES_TEXT = "\n".join(f"- {k}: {v}" for k, v in INCIDENT_TYPES.items())

TRIAGE_PROMPT = """Classify this incident.

Incident types:
{types}

root_cause_service is the service that must be FIXED (e.g. for a client with a bad DB password, the client, not the DB).
Topology analysis (rules): root-cause suspects={suspects}, symptoms={symptoms}.
confidence is 0-1. evidence: short quotes from the logs/health below.

Dependency graph (service -> depends on): {topology}
Health: {health}
Metrics: {metrics}

Compressed logs (count x template):
{digest}"""

INVESTIGATE_PROMPT = """You are gathering extra evidence for this incident before diagnosis.
Current triage: {triage}
Evidence gathered so far:
{evidence}

Pick ONE next read-only tool call, or tool "done" (service "none") if the evidence is sufficient.
Tools: get_logs(service), get_metrics(service), get_health(none). {left} call(s) left. Do not repeat a call."""

DIAGNOSE_PROMPT = """Diagnose the root cause. Rank hypotheses by likelihood (0-1). Separate root causes from
symptoms using the dependency graph: a service failing only because a dependency failed is a symptom.

Incident types:
{types}

root_cause_service is the service that must be FIXED.

Evidence pack:
{pack}"""

PLAN_PROMPT = """Propose the minimal remediation for this diagnosis using ONLY these actions:
{allowlist}

Diagnosis: {diagnosis}
Previously tried (did not resolve): {tried}
Dependency graph: {topology}"""
