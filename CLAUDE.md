# Recovery Agent — Misk x HUMAIN Hackathon, Challenge 1

**Goal:** an AI agent that detects, diagnoses, fixes, and verifies recovery of a failing system, including multiple simultaneous faults.
**Deadline:** Saturday 8 PM Riyadh time. A working end-to-end path matters more than completeness.

**Differentiator:** hybrid model routing. The small local model (Ollama `qwen3.5:4b`, native macOS/Metal) handles routine steps. The big model (`claude-sonnet-5-5` via the `anthropic` SDK) is used only when `router/rules.py` escalates: low confidence, multiple services affected, an unseen incident type, or a failed fix. Every step logs its tier, model, tokens, latency, and cost to SQLite.

## Architecture
```
detect (rules) → compress logs (drain3) → triage (small) → [investigate → diagnose (router) → plan (runbook first) → act (allowlist) → verify] ×≤3 → postmortem → runbooks/
```
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
| `agent/stages.py` | one function per stage `(incident, adapter)` |
| `agent/run.py` | plain-Python state machine loop |
| `chaos.py` | fault injection (`1`, `2`, `reset`) |
| `demo/` | docker-compose target system: api, worker, db, cache, traffic |
| `runbooks/` | postmortems saved as reusable runbooks |

## Conventions
- The agent runs on the host. Ollama runs natively on `localhost:11434` (containers would use `host.docker.internal:11434`).
- Each module has a `__main__` self-check where useful (`python -m router.rules`, `-m llm.backends`, `-m adapters.docker_adapter`).
- Stages mutate `Incident` and call `telemetry.log_step` exactly once.
- `.env` is gitignored. Never commit keys.
- The demo api reads `/config/app.env` (a mounted file), so a "bad deploy" is a config edit plus a restart. chaos saves the previous version to `app.env.prev`.

## Run
```bash
uv sync
cd demo && docker compose up -d --build && cd ..
uv run python -m adapters.docker_adapter   # observe
uv run chaos.py 1                          # inject (1, 2, reset)
uv run python -m agent.run                 # agent pass
uv run python -m llm.backends small big    # test LLM backends
sqlite3 telemetry.db "select * from steps"
```

## Progress
- [x] Env: brew, Docker 29.6 (7.7GB VM), Python 3.11 (uv), Ollama 0.40.12 + qwen3.5:4b (brew service)
- [x] Demo compose up and healthy (~1.2GB of limits)
- [x] chaos scenarios 1 (redis down) and 2 (bad DB password) verified, plus reset
- [x] DockerAdapter read methods + `restart`
- [x] OllamaBackend tested; AnthropicBackend written (needs key in `.env`)
- [x] Router rules + self-check; telemetry to SQLite
- [x] Agent loop; `detect` real (topology separates root cause from symptom); other stages are stubs
- [ ] drain3 compress, small-model triage, investigate tool loop, diagnose via router, runbook plan, act w/ approval + rollback, metric-baseline verify, postmortem→runbook
- [ ] Actions: rollback_config, scale, clear_disk
- [ ] chaos 3–5
- [ ] Dashboard
