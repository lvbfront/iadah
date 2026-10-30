#!/usr/bin/env bash
# One command: fail-safe tests → smoke run → benchmark → regression check against the committed v2 results.
#
#   scripts/eval.sh          quick: hybrid, cold+warm, n=1, scenarios 0–6   (~40 min incl. fail-safe, ~$0.15)
#   scripts/eval.sh full     all modes, cold+warm, n=3, scenarios 0–6       (~4.5 h, ~$1.5)
#
# Needs: Docker Desktop with the demo stack up, Ollama with qwen3.5:4b, ANTHROPIC_API_KEY in .env.
# Results go to benchmarks/eval/<timestamp>/ (gitignored); the committed results are never touched.
# Exit code is non-zero if any step fails or the regression check finds a regression.
set -euo pipefail
cd "$(dirname "$0")/.."
level=${1:-quick}
out=benchmarks/eval/$(date +%Y%m%d-%H%M%S)
mkdir -p "$out"
keepawake=(); command -v caffeinate >/dev/null && keepawake=(caffeinate -dimsu)  # macOS: no sleep mid-run

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

step "preflight"
curl -sf localhost:8000/health >/dev/null || { echo "demo stack is down: cd demo && docker compose up -d --build"; exit 1; }
curl -sf localhost:11434/api/tags >/dev/null || { echo "Ollama is not running: brew services start ollama"; exit 1; }
grep -q '^ANTHROPIC_API_KEY=.\+' .env 2>/dev/null || { echo "set ANTHROPIC_API_KEY in .env"; exit 1; }
uv run python -m router.rules

step "fail-safe tests (break the agent on purpose)"
"${keepawake[@]}" uv run python -m tests.failsafe 2>&1 | tee "$out/failsafe.log"

step "smoke run (scenario 1, hybrid, warm)"
"${keepawake[@]}" uv run python -u -m benchmarks.run --n 1 --scenarios 1 --modes hybrid --conditions warm \
  --out "$out/smoke" | tail -3
python3 -c 'import csv,sys; r=list(csv.DictReader(open(sys.argv[1])))[-1]; sys.exit(r["correct_and_recovered"] != "1")' \
  "$out/smoke/results.csv" || { echo "smoke run did not recover correctly"; exit 1; }

step "benchmark ($level)"
if [ "$level" = full ]; then args=(--n 3); else args=(--n 1 --modes hybrid); fi
"${keepawake[@]}" uv run python -u -m benchmarks.run "${args[@]}" --out "$out/bench" > "$out/bench.log" 2>&1
grep -E '^\[' "$out/bench.log" | tail -20

step "regression check vs committed v2"
uv run python -m benchmarks.compare regress "$out/bench"
echo "results: $out"
