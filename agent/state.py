from dataclasses import dataclass, field


@dataclass
class Incident:
    id: str
    scenario: str = ""
    injected_at: float = 0.0
    detected_at: float = 0.0
    recovered_at: float = 0.0
    stage: str = "detect"
    baseline: dict = field(default_factory=dict)       # service -> {"error_rate", "avg_latency_ms"} pre-fault
    health: dict = field(default_factory=dict)         # latest health snapshot
    unhealthy: dict = field(default_factory=dict)      # service -> why
    suspects: list[str] = field(default_factory=list)  # likely root causes (deps healthy)
    symptoms: list[str] = field(default_factory=list)  # unhealthy because a dep is
    log_digest: dict = field(default_factory=dict)     # service -> ["count x template", ...]
    raw_log_tokens: int = 0
    compressed_log_tokens: int = 0
    triage: dict = field(default_factory=dict)
    tier: str = "small"
    route_reason: str = ""
    escalations: int = 0
    evidence: list[str] = field(default_factory=list)
    diagnosis: dict = field(default_factory=dict)
    root_service: str = ""
    plan: list[dict] = field(default_factory=list)     # [{"action", "service", "reason"}]
    plan_source: str = ""                              # "runbook:<name>" | "model"
    actions_taken: list[dict] = field(default_factory=list)  # adapter action records (with undo info)
    attempts: list[dict] = field(default_factory=list)       # failed iterations: actions + verify report
    iteration: int = 0
    failed_fix: bool = False
    resolved: bool = False
    runbook_saved: str = ""
