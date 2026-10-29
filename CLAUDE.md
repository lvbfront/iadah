# Recovery Agent — Misk x HUMAIN Hackathon, Challenge 1

**Goal:** an AI agent that detects, diagnoses, fixes, and verifies recovery of a failing system, including multiple simultaneous faults.
**Deadline:** Saturday 8 PM Riyadh time. A working end-to-end path matters more than completeness.

**Differentiator:** hybrid model routing. The small local model (Ollama `qwen3.5:4b`, native macOS/Metal) handles routine steps. The big model (`claude-sonnet-5-5` via the `anthropic` SDK) is used only when `router/rules.py` escalates: low confidence, multiple services affected, an unseen incident type, or a failed fix. Every step logs its tier, model, tokens, latency, and cost to SQLite.

## Architecture
```
inject → detect (rules, polled) → compress (drain3) → triage (small)
  → [route → investigate (≤5 read-only tool calls) → diagnose → plan (runbook first) → act (allowlist + approval) → verify (health + baseline)] ×≤3
  → postmortem (reports/*.md + save/update runbooks/*.yaml)
```
- **Routing** (`router/rules.py`): escalate to big on invalid JSON twice, failed fix, >1 independent root cause (after topology removes symptoms), unseen type (no runbook), confidence < 0.7. `--mode big-only|small-only` overrides. Every decision is logged as a `route` step with its reason.
- **JSON stages** (`stages.ask_json`): validate → retry once with error feedback → escalate. Ollama's qwen3.5 build has no structured output, so the schema goes in the prompt. Claude uses `output_config` json_schema.
- **Root-cause guard**: if the model names a healthy service as the root, the topology suspect is used instead (e.g. db_auth_failure → fix the api's config, not the db).
- **Actions**: `agent/schemas.py:ALLOWLIST` with risk tiers. `restart` = low (auto). `rollback_config` = high (y/n prompt, or `--auto-approve`). Each adapter action returns a record with undo info. A failure mid-plan undoes the plan's earlier actions.
- **Last-known-good config**: `.state/lkg/<project>/<service>`, saved at baseline and after every verified recovery. Config is read and written through the Docker archive API at the path in the `recovery.config_path` label.
- **Verify**: all containers healthy AND the api's 10s-window error rate ≤ baseline+0.05 and latency ≤ max(3×, +50ms), polled up to 45s.
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
| `chaos.py` | fault injection (`1`, `2`, `reset`) |
| `demo/` | docker-compose target system: api, worker, db, cache, traffic |
| `runbooks/` | postmortems saved as reusable runbooks |

## Conventions
- The agent runs on the host. Ollama runs natively on `localhost:11434` (containers would use `host.docker.internal:11434`).
- Each module has a `__main__` self-check where useful (`python -m router.rules`, `-m llm.backends`, `-m adapters.docker_adapter`).
- Stages mutate `Incident`. Every LLM call and every decision goes through `telemetry.log_step`, which also prints the live timeline row.
- `.env` is gitignored. Never commit keys.
- The demo api reads `/config/app.env` (a mounted file), so a "bad deploy" is a config edit plus a restart. chaos saves the previous version to `app.env.prev`.

## Run
```bash
uv sync
cd demo && docker compose up -d --build && cd ..
uv run python -m adapters.docker_adapter   # observe
uv run python -m agent.run --scenario 1                 # inject + recover, live timeline
uv run python -m agent.run --scenario 2 --auto-approve  # high-risk rollback without prompt
uv run python -m agent.run --scenario 1 --mode big-only # comparison modes
uv run chaos.py reset                      # manual chaos: 1, 2, reset
uv run python -m llm.backends small big    # test LLM backends
sqlite3 -header telemetry.db "select scenario,mode,mttd_s,mttr_s,escalations,total_cost,raw_log_tokens,compressed_log_tokens from incidents"
```

## Progress
- [x] Env: brew, Docker 29.6, Python 3.11 (uv), Ollama 0.40.12 + qwen3.5:4b, Anthropic key verified
- [x] Demo compose (~1.2GB of memory limits), api `/metrics` uses a 10s window
- [x] **Scenario 1 end-to-end** (hybrid: small only, $0, MTTR ~31–43s)
- [x] **Scenario 2 end-to-end** (hybrid: small only, $0, MTTR ~29s, rollback_config with approval)
- [x] Failed fix → iteration 2 escalates to Sonnet → recovers (tested deny-then-approve)
- [x] Learned runbook: unseen type → big planner → runbook saved → repeat handled by small ($0.017 → $0)
- [x] `--mode` hybrid / big-only / small-only; MTTD/MTTR + compression stats in `incidents`
- [ ] chaos 3–5 (memory leak, disk/slow queries, redis + pool exhaustion) + actions `scale`, `clear_disk`
- [ ] Benchmark script: N runs × 3 modes → comparison table for the pitch
- [ ] Dashboard
