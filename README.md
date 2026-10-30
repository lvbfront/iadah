# Recovery Agent

This agent detects, diagnoses, fixes, and verifies recovery of a failing system. Routine steps run on a local model (Ollama `qwen3.5:4b`). The agent escalates to Claude Sonnet only for hard reasoning. Every step is logged with its model, tokens, latency, and cost.

## Prerequisites
macOS with Homebrew, Docker Desktop running, and:
```bash
brew install uv ollama
brew services start ollama
ollama pull qwen3.5:4b
```

## Setup
```bash
uv sync
cp .env.example .env   # then set ANTHROPIC_API_KEY
```

## Run
```bash
# 1. start the demo system (api :8000, worker, postgres, redis, traffic)
cd demo && docker compose up -d --build && cd ..
curl localhost:8000/health

# 2. inject a fault and let the agent recover it, with a live timeline
uv run python -m agent.run --scenario 1                    # asks y/n before every high-risk action
uv run python -m agent.run --scenario 2 --auto-approve     # skip the y/n (demos/benchmarks only)
uv run python -m agent.run --scenario 1 --mode big-only    # hybrid | big-only | small-only

# scenarios: 0 no fault (control) · 1 redis down · 2 bad DB password after deploy · 3 worker memory leak
#            4 disk fill · 5 redis down + DB connection exhaustion · 6 HELD-OUT wrong REDIS_HOST after deploy
uv run chaos.py list
uv run chaos.py reset

# 3. learned runbooks wait for review; approve one to let the agent reuse it automatically
uv run python -m agent.runbooks list
uv run python -m agent.runbooks approve cache_down+db_pool_exhaustion.learned
```
Incident reports land in `reports/`.

## Dashboard (live demo)
```bash
uv run python -m dashboard.app      # then open http://localhost:8080
```
- **Live**: pick a fault (0–6) and a mode, then **Inject & run**. The topology turns red at the root cause and amber at symptoms. The timeline shows each stage with a RULES / SMALL / BIG / HUMAN badge, plus latency and cost. Counters show MTTD/MTTR, tokens, cost, escalations and the small vs big split.
- **Human approval**: high-risk actions wait for Approve/Deny in the page. The CLI keeps its y/n prompt.
- **Runbooks**: approve pending learned runbooks, delete learned ones, and hide or restore a seed for the learning demo (`python -m agent.runbooks hide|restore|delete` does the same from the CLI).
- **Replay**: replays any past incident at real speed. Runs started from the dashboard (●) replay with topology; CLI and benchmark runs (○) replay the timeline only.
- **Benchmark**: the v2 table, v1 → v2, scenario 0 and scenario 6, read from `benchmarks/`.

## Benchmark
```bash
caffeinate -dimsu uv run python -u -m benchmarks.run > benchmarks/run.log 2>&1   # 7 scenarios × 3 modes × cold/warm × 3
uv run python -m benchmarks.run --n 1 --scenarios 5 --modes hybrid
uv run python -m benchmarks.run --report-only     # re-render benchmarks/results.md from results.csv
```
Results: `benchmarks/results.md`. The pre-audit v1 run is archived in `benchmarks/v1/`.

## Checks
```bash
uv run python -m llm.backends small big   # one call to each backend
uv run python -m router.rules             # routing rules self-check
uv run python -m adapters.docker_adapter  # topology, health, metrics, logs
uv run python -m tests.failsafe           # break the agent: Ollama down, Claude down, bad JSON, no topology, action throws
```

## What the agent will never do
- Run an action that isn't on the allowlist (`agent/schemas.py`), or target a service outside the live topology.
- Run a high-risk action without approval: `rollback_config`, `kill_idle_connections`, `clear_disk`, or a restart or scale on a stateful service (`recovery.stateful`). Without `--auto-approve` a human answers y/n each time.
- Delete files outside a service's declared scratch directory (`recovery.scratch_path`).
- Act on a diagnosis that names a healthy service or only symptoms, act when no model produced a valid diagnosis or plan, or act on an "unknown" incident without escalating to the big model.
- Exceed 6 executed operations per incident (reverted actions and undos count) or 4 steps per plan. Hitting the cap hands the incident to a human.
- Keep an action that made health worse when it can be undone. If it can't be undone, the agent stops and hands over.
- Apply a learned runbook automatically before a human approves it.
- Roll back to a last-known-good config older than 6 hours.
- Declare recovery before 3 consecutive healthy polls spanning at least 20s, with metrics back near the pre-fault baseline and the fault-specific check passing.

## Known limitations
See the "Known limitations" section in `CLAUDE.md`.
