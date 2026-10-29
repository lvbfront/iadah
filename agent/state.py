from dataclasses import dataclass, field


@dataclass
class Incident:
    id: str
    stage: str = "detect"
    unhealthy: dict = field(default_factory=dict)    # service -> health info
    suspects: list[str] = field(default_factory=list)  # likely root causes (deps healthy)
    symptoms: list[str] = field(default_factory=list)  # unhealthy because a dep is
    log_digest: dict = field(default_factory=dict)     # service -> compressed log templates
    triage: dict = field(default_factory=dict)         # {"type", "confidence", ...}
    evidence: list[str] = field(default_factory=list)
    diagnosis: dict = field(default_factory=dict)
    plan: list[dict] = field(default_factory=list)     # [{"action", "service", "risk"}]
    actions_taken: list[dict] = field(default_factory=list)
    iteration: int = 0
    failed_fix: bool = False
    resolved: bool = False
