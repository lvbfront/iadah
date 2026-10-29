# Recovery Agent — Misk x HUMAIN Hackathon, Challenge 1

**Goal:** an AI agent that detects, diagnoses, fixes, and verifies recovery of a failing system, including multiple simultaneous faults.
**Deadline:** Saturday 8 PM Riyadh time. A working end-to-end path matters more than completeness.

**Differentiator:** hybrid model routing. The small local model (Ollama `qwen3.5:4b`, native macOS/Metal) handles routine steps. The big model (`claude-sonnet-5-5` via the `anthropic` SDK) is used only when `router/rules.py` escalates: low confidence, multiple independent root causes, an unseen incident signature, or a failed fix. Every step logs its tier, model, tokens, latency, and cost to SQLite.

## Architecture
```
inject → detect (rules, polled; 3s alert-grouping window) → compress (drain3) → gather (parallel metric reads, code)
  → triage (small) → [route → FAST PATH? (conf ≥ 0.85 + runbook for the signature → skip to plan)
                       | investigate (LLM tool loop, only if ambiguous) → diagnose]
  → plan (runbook first; dependency order) → act (allowlist + approval; per-fix verify) → verify (system + baseline)] ×≤3
  → postmortem (reports/*.md + save/update runbooks/*.yaml)
```
- **Signatures**: diagnoses carry `root_causes: [{incident_type, service}]`; the incident signature is the sorted set of types (a combo like `cache_down+db_pool_exhaustion` is its own signature/runbook). Runbook actions target `$root:<incident_type>`.
- **Routing** (`router/rules.py`): escalate to big on invalid JSON twice, failed fix, >1 independent root cause (topology suspects or triage roots), unseen signature (no runbook that explains every suspect), confidence < 0.7. `--mode big-only|small-only` overrides. Every decision is logged as a `route` step with its reason.
- **JSON stages** (`stages.ask_json`): validate → retry once with error feedback → escalate. Ollama's qwen3.5 build has no structured output, so the schema goes in the prompt. Claude uses `output_config` json_schema.
- **Root-cause guard**: if the model names a healthy service as a root, an unclaimed topology suspect is used instead (e.g. db_auth_failure → fix the api's config, not the db).
- **Actions**: `agent/schemas.py:ALLOWLIST` with risk tiers. Low (auto): `restart`, `scale` (≤5 replicas). High (y/n, or `--auto-approve`): `rollback_config`, `kill_idle_connections`, `clear_disk`. Each action returns a record with undo info (rollback_config and scale are undoable). A failure mid-plan undoes the plan's earlier actions. Actions are sorted by dependency depth (leaf deps first). After each one, that service must be healthy (`verify-fix`) before the next runs.
- **Adapter hooks via labels**: `recovery.depends_on`, `recovery.metrics_url`, `recovery.config_path` (rollback target), `recovery.kill_idle_cmd` (DB admin command), `recovery.scratch_path` (the only dir clear_disk may empty).
- **Last-known-good config**: `.state/lkg/<project>/<service>`, saved at baseline and after every verified recovery. Config is read and written through the Docker archive API at the path in the `recovery.config_path` label.
- **Detect/verify rules**: container down/unhealthy, memory > 75% of limit, app error rate > baseline+0.2 (verify: +0.05), latency > max(3×, +200ms) (verify: +50ms). Verify polls for up to 45s.
- **Learning**: a resolved incident saves a runbook for its signature from every action that ran (across iterations). A runbook step already tried on a now-healthy service is dropped; a tried step on a still-broken service sends the plan to the planner model.
- **Telemetry**: `steps` (tier, requested_model, model actually answering, tokens, latency, cost, note) and `incidents` (MTTD, MTTR, raw vs compressed log tokens, escalations, cost). MTTD/MTTR are measured from when the chaos call returns.
- **Log windows** start 15s before injection, so leftover logs from earlier incidents don't pollute the evidence.

The agent never touches Docker directly. It goes through `adapters/base.py` (`Observability`, `Actions`), so K8s, Prometheus, or ELK adapters can be added later without changing the agent.

## Layout
| Path | What |
|---|---|
| `config.py` | env + model names + pricing table (USD/MTok); local cost = 0 |
| `adapters/base.py` | abstract interfaces |
| `adapters/docker_adapter.py` | Docker SDK impl; topology from `recovery.depends_on` container labels |
| `llm/backends.py` | `LLMResult`, `OllamaBackend`, `AnthropicBackend`, `get_backend(tier)` |
| `router/rules.py` | `pick_tier(...) -> (tier, reason)` |
| `telemetry/__init__.py` | `log_step(...)` → `telemetry.db` table `steps` |
| `agent/state.py` | `Incident` dataclass (the state) |
| `agent/schemas.py` | JSON schemas, prompts, incident types, action allowlist |
| `agent/stages.py` | one function per stage `(incident, adapter)` + `ask_json`, drain3 `compress_lines` |
| `agent/run.py` | CLI runner: baseline → inject → detect poll → loop → postmortem → summary |
| `reports/` | incident reports (gitignored) |
| `chaos.py` | fault injection 1–5 + `reset`; `TRUTH` = ground-truth root causes per scenario |
| `benchmarks/run.py` | scenario × mode × cold/warm × N benchmark → `benchmarks/results.{csv,md}`, `summary.csv` |
| `demo/` | docker-compose target system: api, worker, db, cache, traffic |
| `runbooks/` | postmortems saved as reusable runbooks |

## Conventions
- The agent runs on the host. Ollama runs natively on `localhost:11434` (containers would use `host.docker.internal:11434`).
- Each module has a `__main__` self-check where useful (`python -m router.rules`, `-m llm.backends`, `-m adapters.docker_adapter`).
- Stages mutate `Incident`. Every LLM call and every decision goes through `telemetry.log_step`, which also prints the live timeline row.
- `.env` is gitignored. Never commit keys.
- The demo api reads `/config/app.env` (a mounted file), so a "bad deploy" is a config edit plus a restart. chaos saves the previous version to `app.env.prev`.
- Demo Postgres: superuser `postgres`; the `app` and `reporting` roles are non-superusers and `max_connections=30`, so connection exhaustion is real. db-init.sql only runs on a fresh volume (`docker compose up -d --force-recreate --renew-anon-volumes db`).
- Scenarios: 1 redis down · 2 bad DB password after deploy · 3 worker memory leak (8MB/s) · 4 api scratch tmpfs full (ENOSPC) · 5 redis down + DB connection exhaustion (runaway `reporting` job).
- `agent.run --scenario N` always runs `chaos.reset()` first, so every run starts from the same state.
- Runbooks in git are seeds (`source: seed`, `times_used: 0`). Reset counters before committing. The scenario-5 combo is deliberately unseeded.

## Run
```bash
uv sync
cd demo && docker compose up -d --build && cd ..
uv run python -m adapters.docker_adapter   # observe
uv run python -m agent.run --scenario 1                 # inject + recover, live timeline
uv run python -m agent.run --scenario 2 --auto-approve  # high-risk rollback without prompt
uv run python -m agent.run --scenario 1 --mode big-only # comparison modes
uv run chaos.py reset                      # manual chaos: 1-5, reset
uv run python -m benchmarks.run --n 3      # full benchmark (resumable); --report-only to re-render
uv run python -m llm.backends small big    # test LLM backends
sqlite3 -header telemetry.db "select scenario,mode,mttd_s,mttr_s,escalations,total_cost,raw_log_tokens,compressed_log_tokens from incidents"
```

## Progress
- [x] Env: brew, Docker 29.6, Python 3.11 (uv), Ollama 0.40.12 + qwen3.5:4b, Anthropic key verified
- [x] Scenarios 1–5 end-to-end. Hybrid warm: 1–4 on the fast path with the small model at $0. 5 escalates (2 roots), fixes in dependency order, and learns a combo runbook.
- [x] Fast path + code-gathered evidence: MTTR s1 37→19s, s2 29→20s
- [x] Failed-fix escalation, deny-then-approve, undo, learned runbooks
- [x] Benchmark script (resumable, unattended); results in `benchmarks/results.md`
- [ ] Small model can't label db_pool_exhaustion (says unknown/slow_queries), so scenario 5 always needs Sonnet to diagnose
- [ ] Slow-queries variant of scenario 4 (lock-holding idle-in-transaction job → kill_idle_connections)
- [ ] Dashboard
