import os

from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
SMALL_MODEL = os.getenv("SMALL_MODEL", "qwen3.5:4b")
BIG_MODEL = os.getenv("BIG_MODEL", "claude-sonnet-5-5")

# Compose project the DockerAdapter watches.
TARGET_PROJECT = os.getenv("TARGET_PROJECT", "demo")
API_URL = os.getenv("API_URL", "http://localhost:8000")

# USD per 1M tokens (input, output). Local models are free.
PRICING = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

TELEMETRY_DB = os.getenv("TELEMETRY_DB", "telemetry.db")
MAX_VERIFY_ITERATIONS = 3


def cost(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return (in_tok * pin + out_tok * pout) / 1_000_000
