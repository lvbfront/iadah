# Recovery Agent — Misk x HUMAIN Hackathon, Challenge 1

**Goal:** an AI agent that detects, diagnoses, fixes, and verifies recovery of a failing system, including multiple simultaneous faults.
**Deadline:** Saturday 8 PM Riyadh time. A working end-to-end path matters more than completeness.

**Differentiator:** hybrid model routing. The small local model (Ollama `qwen3.5:4b`, native macOS/Metal) handles routine steps. The big model (`claude-sonnet-5-5` via the `anthropic` SDK) is used only when `router/rules.py` escalates: unknown type, low confidence, multiple independent root causes, an unseen incident signature, a failed fix, or a failed or invalid small-model call. Every step logs its tier, the model that actually answered, tokens, latency, and cost to SQLite.

## Architecture
```
inject → detect (rules, polled; 3s alert-grouping window) → compress (drain3) → gather (parallel metric reads, code)
  → triage (small) → [route → FAST PATH? (conf ≥ 0.85 + approved runbook + triage roots == topology suspects)
                       | investigate (LLM tool loop, only if ambiguous) → diagnose]
  → plan (approved runbook first; ≤4 steps; dependency order) → act (allowlist, per-service risk, approval,
    per-fix verify, auto-undo if worse, ≤6 ops) → verify (3 healthy polls over ≥20s + baseline + fault checks)] ×≤3
  → postmortem (reports/*.md; learned runbook → <sig>.learned.yaml, pending review) → invariants
```
- **Signatures**: diagnoses carry `root_causes: [{incident_type, service}]`. The signature is the sorted set of types, so a combo is its own signature. Runbook actions target `$root:<incident_type>`.
- **Consistency gate** (`stages.roots_consistent`): a triage or diagnosis must name only unhealthy services, and at least one that isn't a topology symptom. Otherwise it's treated as invalid output: retry once with a constraint hint, then escalate, then hand to a human. It is never silently rewritten. Topology symptoms are dropped from roots afterwards.
- **Routing** (`router/rules.py`): mode override first. Then: failed/invalid small output, failed fix, unknown type, >1 independent root cause, unseen signature (no *approved* runbook covering every suspect), confidence < 0.7 → big. Every decision is logged as a `route` step with its reason.
- **JSON stages** (`stages.ask_json`): model exceptions are treated like invalid output. Validate → retry once with error feedback → escalate. Ollama's qwen3.5 build has no structured output, so the schema goes in the prompt. Claude uses `output_config` json_schema. A refusal counts as invalid output, and its tokens are logged.
- **Fail safe**: no valid diagnosis or plan, or "unknown" with no big model allowed → `needs_human`, no actions. An iteration with no alert after a failed verify re-verifies only and never takes new actions.
- **Actions**: `agent/schemas.py:ALLOWLIST`. Low (auto): `restart`, `scale` (≤5). High (y/n, or `--auto-approve`): `rollback_config`, `kill_idle_connections`, `clear_disk`, plus restart/scale on a `recovery.stateful` service (`stages.risk_of`).
  - Restarts of multi-replica services are rolling.
  - A failure mid-plan undoes the plan's earlier actions.
  - After each action the agent waits for the service to come back (`verify-fix`) and compares the flagged-service count with before. If it's worse, the action is undone, or the agent stops and hands over if it can't be undone.
  - Caps: 6 executed operations per incident (reverted actions and undos count), 4 plan steps.
- **Adapter hooks via labels**: `recovery.depends_on`, `recovery.metrics_url`, `recovery.config_path`, `recovery.kill_idle_cmd`, `recovery.conn_usage_cmd`, `recovery.scratch_path`, `recovery.stateful`.
- **Last-known-good config**: `.state/lkg/<project>/<service>`, saved at baseline and after every verified recovery. Rollback refuses an LKG older than 6h. api and worker have separate config dirs (`demo/config/{api,worker}`).
- **Detect/verify rules** (`stages.flags`):
  - Detect (loose): down/unhealthy, memory > 75%, error rate > baseline+0.2, latency > max(3×, +200ms).
  - Verify (strict): also fails on "starting" and missing metrics; error tolerance +0.05, latency +50ms.
  - Verify also requires 3 consecutive passing polls over ≥20s (monotonic clock), plus fault checks per diagnosed root: db connection usage ≤ 70%, memory not growing > 10pp, scratch disk ≤ 80%, root service healthy.
- **Baseline**: taken after a full 10s metrics window with 0 errors, 5 samples.
- **Learning**: a resolved incident writes `<signature>.learned.yaml` with `status: pending_review` from every action that ran. Only `status: approved` runbooks are used (fast path, routing, plan). Approve with `python -m agent.runbooks approve <name>`. Seed and approved files are never overwritten.
- **Invariants** (`agent/invariants.py`), checked after every incident:
  - Every high-risk action was approved, and auto-approval only happens under `--auto-approve`.
  - The approval count in telemetry matches the high-risk actions.
  - The act steps in telemetry match the agent's records.
  - Executed operations ≤ cap.
  - Recovery comes only after a ≥20s stable window.
  - Each step's cost equals price × tokens, and the incident cost equals the sum of its steps.
- **Telemetry**: `steps` and `incidents` tables. The MTTD/MTTR clock starts *before* injection.

**Dashboard** (`dashboard/`): runs the same `agent.run.run()` on one worker thread. It has exactly two hooks into the agent: `telemetry.LISTENERS` (live step feed) and `stages.approver` (approval provider, default `cli_approver` y/n; the dashboard swaps in a web Approve/Deny that denies after 10 min). It sees the live `Incident` through a subclass, without changing run(). Live runs are recorded to `.state/dashboard/<incident>.jsonl` for real-speed replay; CLI/benchmark incidents replay as timeline only. The page is a fixed 1920×1080 stage scaled to the window. Hiding a seed moves it to `.state/hidden_runbooks/`.

The agent never touches Docker directly. It goes through `adapters/base.py` (`Observability`, `Actions`).

## Layout
| Path | What |
|---|---|
| `config.py` | env, model names, pricing, thresholds, caps |
| `adapters/base.py`, `adapters/docker_adapter.py` | interfaces; Docker SDK impl (label-driven) |
| `llm/backends.py` | `OllamaBackend`, `AnthropicBackend`, `get_backend(tier)`, `parse_json`, `validate` |
| `router/rules.py` | `pick_tier(...) -> (tier, reason)` |
| `telemetry/__init__.py` | `log_step`, incidents table, live timeline printer |
| `agent/state.py` | `Incident` dataclass |
| `agent/schemas.py` | schemas, prompts, incident types (generic), allowlist |
| `agent/stages.py` | stages + `ask_json`, `flags`, `fault_checks`, `roots_consistent`, `risk_of` |
| `agent/run.py` | CLI runner |
| `agent/invariants.py` | post-incident invariant checks |
| `agent/runbooks.py` | `list` / `approve` / `hide` / `restore` / `delete` for runbooks (CLI + used by the dashboard) |
| `dashboard/app.py`, `dashboard/index.html` | live demo dashboard (FastAPI + SSE, one static page), replay, web approval |
| `chaos.py` | scenarios 0–6 + `reset`; `TRUTH`, `EXPECTED_ACTIONS`, `diag_correct` |
| `benchmarks/run.py` | benchmark (resumable, sleep guard) → `benchmarks/results.{csv,md}`; v1 in `benchmarks/v1/` |
| `tests/failsafe.py` | break-the-agent cases (Ollama down, Claude down, bad JSON, no topology, action throws) |
| `demo/` | target system: api, worker, db, cache, traffic |
| `runbooks/` | seed runbooks (approved) + learned (`*.learned.yaml`, pending review) |

## Conventions
- The agent runs on the host. Ollama runs natively on `localhost:11434`.
- Self-checks: `python -m router.rules`, `-m llm.backends`, `-m adapters.docker_adapter`, `-m tests.failsafe`.
- Every LLM call and decision goes through `telemetry.log_step`, which also prints the live timeline row.
- `.env` is gitignored. Never commit keys.
- Demo Postgres: superuser `postgres`; `app` and `reporting` are non-superusers, `max_connections=30`. db-init.sql runs only on a fresh volume.
- Scenarios:
  - 0: control
  - 1: redis down
  - 2: bad DB password after deploy
  - 3: worker memory leak
  - 4: api scratch tmpfs full
  - 5: redis down + DB connection exhaustion
  - 6: **held out**, wrong REDIS_HOST after deploy, graded on the root service. No prompt or runbook was tuned for it.
- `agent.run --scenario N` always runs `chaos.reset()` first.
- Runbooks in git are approved seeds with `times_used: 0`. Reset counters before committing. **Never commit a learned scenario-5 combo runbook** (it's recorded live).
- Benchmarks always run under `caffeinate -dimsu`. Runs where the machine slept more than 60s are rerun.

## Decisions made autonomously (overnight 2026-10-30)
- **Verify fault checks** are keyed by the *diagnosed* type. A wrong diagnosis is verified against the wrong check, but the generic strict rules over all services still apply.
- **Scenario 6 truth** is `("*", "api")`: the vocabulary has no type for "wrong hostname in config", and adding one would be tuning on the held-out scenario.
- **The fast-path misfire on scenario 6 is not fixed.** The small model triaged `cache_down@api`, and the cache runbook restarted the api (low risk, caught by verify, escalated, then fixed by rollback). Scoping runbooks per service would be a runbook change motivated by the held-out scenario. Documented as a known limitation instead.
- **"Worse after an action"** = more services flagged by the loose detect rules ACTION_SETTLE_S (5s) after the service is back. Count-based, to avoid noise from error-rate windows.
- **Expected actions** per scenario are strict. restart(db) for scenario 5 and restart(api) for scenario 4 count as unnecessary.
- **Harness errors are rerun, agent failures are not.** In v2, 4 runs failed in 1s inside `chaos.reset()`: `compose up` waits for db to be healthy, and db was still exhausted after a small-only scenario 5 run. `reset` now retries for up to 60s. Only those 4 rows were deleted and rerun; every agent outcome stands as recorded.
- **Primary diagnosis accuracy** grades the agent's final roots (model output minus topology symptoms). Model-raw accuracy is reported alongside.

## Known limitations
- **Incident vocabulary is small and maps closely onto scenarios 1–5** (only `slow_queries` is a decoy). Warm runs are close to scripted. Scenario 6 and the cold condition are the honest tests.
- **Runbooks are keyed by incident type only, not by service.** A misclassified type runs that runbook on whichever service the model named (seen on scenario 6: a needless `restart(api)`).
- **The small model can't label DB connection exhaustion**, so scenario 5 always needs the big model to diagnose.
- **Log digest error pattern** (`stages.ERR`) includes `not known`, added in v1 for scenario 1's DNS errors. It also boosts scenario 6's identical DNS error, so scenario 6 isn't 100% clean.
- **The worker has no healthcheck or metrics.** Verify sees only that it's running and its memory, not whether it's draining jobs.
- **Live mode (no `--scenario`)** has no baseline, so detect relies on absolute rules, and LKG configs may be up to 6h old.
- **Topology comes only from labels.** Missing labels are detected and force escalation; wrong labels are not detected.
- **Confidence is the model's self-report**, gated by consistency with the topology, not calibrated.
- **"Worse after an action" uses the flagged-service count only.**

## Run
```bash
uv sync
cd demo && docker compose up -d --build && cd ..
uv run python -m agent.run --scenario 5                      # y/n prompts for high-risk actions
uv run python -m agent.run --scenario 5 --auto-approve
uv run python -m agent.runbooks list | approve <name>
caffeinate -dimsu uv run python -u -m benchmarks.run > benchmarks/run.log 2>&1
uv run python -m tests.failsafe
uv run python -m dashboard.app                               # → http://localhost:8080
```

## Progress
- [x] Scenarios 0–6 end-to-end, hybrid / big-only / small-only, fast path, learning loop with human approval
- [x] v1 benchmark (pre-audit) archived in `benchmarks/v1/`
- [x] Overnight hardening: strict verify, hint removal, held-out scenario 6, control scenario 0, new metrics, safety (per-service risk, rolling restart, auto-undo, caps, runbook review), fresh-review audit fixes, fail-safe tests 5/5, invariants
- [x] v2 benchmark (126 runs, post-audit) → `benchmarks/results.md`: hybrid 100% correct & recovered, 0 invariant violations, 0 false detections on the control
- [x] Dashboard: live topology/timeline/counters, web approval, runbook review + learning demo (hide/restore seed), replay, benchmark tab. Tested from the page: s1 ($0, fast path), s5 with web approval, learning demo ($0.0146 → $0).
