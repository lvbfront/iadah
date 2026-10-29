import os

from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
SMALL_MODEL = os.getenv("SMALL_MODEL", "qwen3.5:4b")
BIG_MODEL = os.getenv("BIG_MODEL", "claude-sonnet-5-5")

# Compose project the DockerAdapter watches.
TARGET_PROJECT = os.getenv("TARGET_PROJECT", "demo")
STATE_DIR = os.getenv("STATE_DIR", ".state")  # last-known-good configs etc.

# USD per 1M tokens (input, output). Local models are free.
# Includes the server-side fallback targets so a fallback answer is priced correctly.
PRICING = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-4-8": (5.00, 25.00),
}

TELEMETRY_DB = os.getenv("TELEMETRY_DB", "telemetry.db")
MAX_VERIFY_ITERATIONS = 3
INVESTIGATE_BUDGET = 5      # LLM-chosen tool calls per iteration (ambiguous cases only)
FAST_PATH_CONFIDENCE = 0.85 # triage confidence + matching runbook → skip investigate/diagnose
RUNBOOKS_DIR = os.getenv("RUNBOOKS_DIR", "runbooks")
VERIFY_TIMEOUT_S = 90       # give up verifying after this long
STABLE_POLLS = 3            # recovered = this many consecutive healthy polls…
STABILIZE_S = 20            # …spanning at least this long
VERIFY_POLL_S = 3
DETECT_WINDOW_S = 90        # give up waiting for a detection after this long
CONTROL_WINDOW_S = 60       # scenario 0 (no fault): watch a healthy system this long
ALERT_GROUP_S = 3          # after the first alert, wait this long and re-detect to group related alerts

# Set by the CLI runner.
MODE = "hybrid"             # hybrid | big-only | small-only
AUTO_APPROVE = False


def cost(model: str, in_tok: int, out_tok: int, fallback_model: str = "") -> float:
    pin, pout = PRICING.get(model) or PRICING.get(fallback_model, (0.0, 0.0))
    return (in_tok * pin + out_tok * pout) / 1_000_000
