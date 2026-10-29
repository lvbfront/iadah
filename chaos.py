"""Inject faults into the demo system. Usage: uv run chaos.py {1|2|reset|list}"""
import shutil
import subprocess
import sys
from pathlib import Path

DEMO = Path(__file__).parent / "demo"
CFG = DEMO / "config" / "app.env"
PREV = CFG.with_suffix(".env.prev")  # what a rollback restores


def compose(*args):
    subprocess.run(["docker", "compose", "-f", str(DEMO / "docker-compose.yml"), *args], check=True, capture_output=True)


def redis_down():
    compose("stop", "cache")


def bad_db_password():
    """Simulates a deploy that ships a wrong DB password to the api."""
    shutil.copy(CFG, PREV)
    text = "\n".join("DB_PASSWORD=wrong-after-deploy" if l.startswith("DB_PASSWORD=") else l
                     for l in CFG.read_text().splitlines())
    CFG.write_text(text + "\n")
    compose("restart", "api")


def reset():
    if PREV.exists():
        shutil.move(PREV, CFG)
    compose("up", "-d")
    compose("restart", "api", "worker")


SCENARIOS = {
    "1": ("redis down", redis_down),
    "2": ("bad DB password after deploy", bad_db_password),
    # ponytail: 3 (worker leak), 4 (disk fill / slow queries), 5 (redis down + pool exhaustion) not built yet
    "reset": ("restore healthy state", reset),
}

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "list"
    if arg not in SCENARIOS:
        for k, (desc, _) in SCENARIOS.items():
            print(f"  {k:6} {desc}")
        sys.exit(0 if arg == "list" else 1)
    print(f"chaos: {SCENARIOS[arg][0]}")
    SCENARIOS[arg][1]()
