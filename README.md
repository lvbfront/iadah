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

# 2. break it manually
uv run chaos.py list
uv run chaos.py 1        # redis down
uv run chaos.py 2        # bad DB password after "deploy"

# 2+3. or let the runner inject a fault and recover, with a live timeline
uv run python -m agent.run --scenario 1
uv run python -m agent.run --scenario 2 --auto-approve       # skip the y/n for high-risk actions
uv run python -m agent.run --scenario 1 --mode big-only      # hybrid | big-only | small-only

# reports land in reports/, learned runbooks in runbooks/
uv run chaos.py reset
```

## Checks
```bash
uv run python -m llm.backends small big   # one call to each backend
uv run python -m router.rules             # routing rules self-check
uv run python -m adapters.docker_adapter  # topology, health, metrics, logs
sqlite3 telemetry.db "select stage,tier,model,in_tokens,out_tokens,latency_s,cost_usd,note from steps"
sqlite3 -header telemetry.db "select scenario,mode,mttd_s,mttr_s,escalations,total_cost from incidents"
```

See `CLAUDE.md` for architecture and progress.
