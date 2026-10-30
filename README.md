# إعادة | I'adah — Agentic Recovery, Efficient by Design

I'adah is an AI agent that **detects, diagnoses, fixes and verifies recovery** of a failing system, including two faults at the same time. A **small local model** (Ollama `qwen3.5:4b`) handles routine incidents. The agent escalates to **Claude Sonnet** only when rules in code say the incident is hard: an unknown type, low confidence, more than one independent root cause, an unseen signature, a failed fix, or invalid model output. Every step is logged with the model that actually answered, its tokens, latency and cost. Fixes go through an allowlist with risk tiers and human approval for risky actions, and are undone automatically if they make things worse. Recovery is declared only after a sustained healthy window. When an incident is resolved, the agent writes a runbook that a human must approve before it is reused. In 126 benchmark runs, hybrid routing matched the big-only agent's accuracy (100% correct and recovered on 6 fault scenarios, including one held out from tuning) at **39–44% lower cost**, with **0 invariant violations** and **0 false alarms**.

*Misk x HUMAIN hackathon, Challenge 1: Agentic Recovery of a Failing System.*

## Architecture

```
            ┌──────────────── demo system (docker compose) ────────────────┐
            │  traffic → api ──┬──> db (postgres, stateful)                 │
            │           worker ┴──> cache (redis)                           │
            └──────────────▲───────────────────────────┬────────────────────┘
                 actions   │  (allowlist, risk tiers)   │  health · metrics · logs · topology (labels)
                           │                            ▼
 ┌─────────────────────────┴──── adapters/ (Observability + Actions interfaces, DockerAdapter) ─────────┐
 │                                                                                                      │
 │  detect ── rules, polled; alert grouping; topology separates root suspects from symptoms   [RULES]   │
 │  compress ─ drain3 log templates (~18–23× fewer tokens)                                    [RULES]   │
 │  gather ── parallel metric reads in code                                                   [RULES]   │
 │  triage ── small model, JSON-schema validated; consistency gate vs topology                [SMALL]   │
 │  route ─── router/rules.py: escalate? (unknown · low conf · >1 root · unseen · failed fix   [RULES]   │
 │            · invalid output) ──────────────────────────────────────────────┐                         │
 │  FAST PATH: conf ≥ 0.85 + approved runbook + roots == suspects             │                         │
 │       │                    else: investigate (tool loop, if ambiguous) → diagnose      [SMALL|BIG]   │
 │       ▼                                                                                              │
 │  plan ─── approved runbook first, else planner model; ≤4 steps; dependency order       [RULES|BIG]   │
 │  act ──── allowlist · per-service risk · HUMAN approval for high risk · per-fix verify   [HUMAN]     │
 │           · auto-undo if worse · ≤6 operations                                                       │
 │  verify ─ 3 healthy polls over ≥20s + back to baseline + fault-specific checks     (×≤3 iterations)  │
 │  postmortem ─ report + learned runbook (pending human review) · invariants checked                   │
 │                                                                                                      │
 │  telemetry → SQLite: every step's tier, actual model, tokens, latency, cost; MTTD/MTTR               │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Path | What |
|---|---|
| `agent/` | state machine (`run.py`), stages, schemas and allowlist, invariants, runbook review CLI |
| `router/rules.py` | escalation rules: no LLM, every decision logged with its reason |
| `llm/backends.py` | Ollama and Anthropic backends (schema-validated JSON, retry with feedback) |
| `adapters/` | `Observability` and `Actions` interfaces; Docker implementation driven by container labels |
| `telemetry/` | SQLite step log and live timeline |
| `chaos.py` | fault scenarios 0–6 with ground truth |
| `benchmarks/` | benchmark harness and results (v2 current, v1 archived) |
| `dashboard/` | live demo dashboard (FastAPI + SSE, one static page) |
| `demo/` | the target system: api, worker, Postgres, Redis, traffic |

## Quickstart

macOS with Homebrew and Docker Desktop:
```bash
brew install uv ollama && brew services start ollama && ollama pull qwen3.5:4b
uv sync
cp .env.example .env                                 # set ANTHROPIC_API_KEY
cd demo && docker compose up -d --build && cd ..     # api on :8000

uv run python -m dashboard.app                       # live dashboard → http://localhost:8080
uv run python -m agent.run --scenario 5              # or the CLI: y/n prompt before each high-risk action
uv run python -m agent.run --scenario 1 --mode big-only --auto-approve
```

| Scenario | Fault |
|---|---|
| 0 | none (control: the agent must do nothing) |
| 1 | Redis down |
| 2 | bad DB password after a deploy |
| 3 | worker memory leak |
| 4 | api scratch disk full |
| 5 | Redis down **and** DB connection exhaustion (two independent root causes) |
| 6 | **held out:** wrong `REDIS_HOST` after a deploy. No prompt or runbook was tuned for it |

Other commands:
```bash
uv run python -m agent.runbooks list | approve <name> | hide <seed> | restore | delete <learned>
caffeinate -dimsu uv run python -u -m benchmarks.run > benchmarks/run.log 2>&1   # full benchmark, ~4 h
uv run python -m tests.failsafe       # break the agent: Ollama down, Claude down, bad JSON, no topology, action throws
uv run python -m router.rules         # routing self-check
```

**Dashboard:**
- Topology turns red at the root cause and amber at symptoms.
- The timeline shows each stage with a RULES / SMALL / BIG / HUMAN badge, latency and cost. Counters show MTTD, MTTR, tokens, cost, escalations and the small vs big split.
- High-risk actions wait for Approve/Deny in the page.
- Runbook review, plus hide/restore of a seed runbook for a learning demo.
- Real-speed replay of past incidents, and a benchmark tab.

## Results (v2, 126 runs)

Each scenario was run 3 times in each mode (hybrid, big-only, small-only) and each condition. **cold** means no runbooks; **warm** means seed runbooks (none for scenarios 5 and 6). **Correct & recovered** means the right root cause, verified recovery, still healthy 10s later, and no invariant violation. Ground truth lives in `chaos.TRUTH` and is never visible to the agent. Full tables: [`benchmarks/results.md`](benchmarks/results.md).

**Fault scenarios 1–6**

| mode | runbooks | diagnosis | recovered | **correct & recovered** | MTTR s | unnecessary actions / inc | escalations | cost / run |
|---|---|---|---|---|---|---|---|---|
| hybrid | cold | 100% | 100% | **100%** | 66.2 | 0.00 | 1.17 | $0.0175 |
| hybrid | warm | 100% | 100% | **100%** | 85.2 | 0.17 | 0.50 | $0.0121 |
| big-only | cold | 100% | 100% | **100%** | 64.5 | 0.00 | 0 | $0.0315 |
| big-only | warm | 100% | 100% | **100%** | 65.8 | 0.06 | 0 | $0.0197 |
| small-only | cold | 61% | 67% | **61%** | 123.1 | 0.28 | 0 | $0 |
| small-only | warm | 67% | 50% | **50%** | 57.9 | 0.33 | 0 | $0 |

- **Hybrid matches big-only's accuracy at 44% lower cost (cold) and 39% lower cost (warm).**
- **Small-only alone is not enough.** It cannot label DB connection exhaustion (scenario 5 is 0/6). On scenario 2 its triage contradicts the topology, so the consistency gate rejects it and it hands the incident to a human without acting.
- **Hybrid warm MTTR (85s) is inflated by scenario 6.** Over scenarios 1–5 it is 62.6s; see below.
- **No invariant violations in any run:** every high-risk action was approved, telemetry matched what the agent did, the action cap held, and costs reconcile.
- Total API spend for the whole benchmark: **$1.45**.

**Scenario 0, no-fault control:** 18 runs, **0 false detections, 0 actions**, in every mode.

**Scenario 6, held out** (graded on locating the root at the api; the vocabulary has no type for a wrong hostname):

| mode | cold | warm |
|---|---|---|
| hybrid | 3/3, MTTR 59s, $0.018 | 3/3, MTTR 198s, $0.042 |
| big-only | 3/3, MTTR 57s, $0.031 | 3/3, MTTR 101s, $0.037 |
| small-only | 2/3, MTTR 377s | 0/3 |

- **Cold:** a config rollback on the api fixes it in about 1 minute.
- **Hybrid warm misfires first, then recovers.** The small model calls it `cache_down@api`, so the cache runbook restarts the api. Verify catches that, the agent escalates, and Sonnet rolls back the config.
- **The misfire was left unfixed on purpose:** fixing it would mean tuning on the held-out scenario.

### v1 → v2 (scenarios 1–5, like for like)

v1 was the first benchmark. A fresh review of v1 found leaked hints in the prompts, a single-poll verify, and a guard that silently rewrote wrong diagnoses. v2 fixes all three, and its numbers are lower but real.

| mode | runbooks | v1 recovered | v2 correct & recovered | v1 MTTR | v2 MTTR | v1 $/run | v2 $/run |
|---|---|---|---|---|---|---|---|
| hybrid | cold | 100% | 100% | 39.6 | 67.7 | $0.0157 | $0.0174 |
| hybrid | warm | 100% | 100% | 32.9 | 62.6 | $0.0045 | $0.0061 |
| big-only | cold | 100% | 100% | 41.5 | 66.1 | $0.0326 | $0.0317 |
| big-only | warm | 100% | 100% | 36.8 | 58.8 | $0.0229 | $0.0162 |
| small-only | cold | 100%* | 60% | 59.4 | 72.4 | $0 | $0 |
| small-only | warm | 100%* | 60% | 46.4 | 57.9 | $0 | $0 |

- **MTTR is about 25–30s longer**, mostly because verify now requires a stable window of at least 20s. The clock also now starts *before* injection, so detection time counts. The agent is not slower at fixing.
- **Hybrid warm escalates more (0.2 → 0.4 per run).** With hints removed and no silent rewriting, the small model's wrong scenario 2 triage is now rejected and escalated.
- \***v1's small-only 100% was false.** Those recoveries came from single-poll verifies and accidental restarts. v1 is archived in [`benchmarks/v1/`](benchmarks/v1/).

## What the agent will never do
- Run an action that isn't on the allowlist (`agent/schemas.py`), or target a service outside the live topology.
- Run a high-risk action without approval: `rollback_config`, `kill_idle_connections`, `clear_disk`, or a restart or scale of a stateful service (`recovery.stateful`). Without `--auto-approve`, a human approves each one, in the dashboard or as a CLI y/n.
- Delete files outside a service's declared scratch directory (`recovery.scratch_path`).
- Act on a diagnosis that names a healthy service or only symptoms, act when no model produced a valid diagnosis or plan, or act on an "unknown" incident without escalating to the big model.
- Exceed 6 executed operations per incident (reverted actions and undos count) or 4 steps per plan. Hitting a cap hands the incident to a human.
- Keep an action that made health worse when it can be undone. If it can't be undone, the agent stops and hands over.
- Apply a learned runbook before a human approves it.
- Roll back to a last-known-good config older than 6 hours.
- Declare recovery before 3 consecutive healthy polls spanning at least 20s, with metrics back near the pre-fault baseline and the fault-specific check passing.

Fail-safe tests (`tests/failsafe.py`, 5/5 pass):

| Broken on purpose | What the agent does |
|---|---|
| Ollama down | Escalates and recovers |
| Claude down on an incident that needs it | Hands to a human, takes no action |
| Malformed JSON in small-only mode | Hands to a human, takes no action |
| Topology labels missing | Treats the incident as ambiguous, no fast path |
| An action throws mid-plan | Undoes the plan's earlier actions |

## Known limitations
- **The incident vocabulary is small and maps closely onto scenarios 1–5** (only `slow_queries` is a decoy). Warm runs are close to scripted. Scenario 6 and the cold condition are the honest tests.
- **Runbooks are keyed by incident type, not by service.** A misclassified type runs that runbook on whichever service the model named. This caused the needless `restart(api)` on scenario 6.
- **The 4B local model can't label DB connection exhaustion**, so scenario 5 always needs the big model to diagnose.
- **The log error pattern includes `not known`,** added for scenario 1's DNS errors. It also highlights scenario 6's identical DNS error.
- **The worker has no healthcheck or metrics.** Verify sees only that it's running and its memory use, not whether it is draining jobs.
- **Live mode** (no `--scenario`) has no pre-fault baseline, so detection uses absolute thresholds only.
- **Topology comes from container labels.** Missing labels are detected and force escalation; wrong labels are not detected.
- **Confidence is the model's self-report**, gated by consistency with the topology. It is not calibrated.
- **"Made things worse" counts flagged services only**, not severity.
- **One demo system, one machine, n=3 per cell.** The results show the method works, not how it performs in production.

## What's next
- **Service-scoped runbooks** (type × service) and a runbook match check before the fast path, to remove the scenario 6 misfire class.
- **A larger, open incident vocabulary**, plus a held-out suite of several unseen scenarios instead of one.
- **Kubernetes and Prometheus/Loki adapters** behind the same `Observability`/`Actions` interfaces.
- **Calibrated routing:** learn escalation thresholds from telemetry (small-model accuracy per incident type) instead of fixed rules.
- **A fine-tuned or larger local model** for the failure classes the 4B model misses (DB connection exhaustion, config errors).
- **Change awareness:** correlate faults with recent deploys and config diffs, to explain the root cause and not only fix it.
- **Approvals via Slack/Teams**, behind the same pluggable approval provider the dashboard uses.
