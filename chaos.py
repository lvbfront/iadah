"""Inject faults into the demo system. Usage: uv run chaos.py {1|2|reset|list}"""
import shutil
import subprocess
import sys
import time
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


LEAK = "demo-chaos-leak"


def db_conn_exhaustion():
    """A runaway 'reporting' job opens idle connections until Postgres has no slots left for the app."""
    subprocess.run(["docker", "rm", "-f", LEAK], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", LEAK, "--network", "demo_default", "-e", "PGPASSWORD=reporting",
                    "postgres:16-alpine", "sh", "-c",
                    "for i in $(seq 40); do (sleep 100000 | psql -h db -U reporting -d app -q) & sleep 0.05; done; wait"],
                   check=True, capture_output=True)


def _wait_health(container, status, timeout=40):
    t = time.time()
    while time.time() - t < timeout:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
                             capture_output=True, text=True).stdout.strip()
        if out == status:
            return
        time.sleep(1)
    raise RuntimeError(f"{container} never became {status}")


def redis_down_and_db_exhaustion():
    """Two independent faults at once. Waits until the DB is actually exhausted, then kills redis,
    so the agent sees both faults together."""
    db_conn_exhaustion()
    _wait_health("demo-db-1", "unhealthy")
    redis_down()


def reset():
    subprocess.run(["docker", "rm", "-f", LEAK], capture_output=True)
    if PREV.exists():
        shutil.move(PREV, CFG)
    compose("up", "-d")
    compose("restart", "api", "worker")


SCENARIOS = {
    "1": ("redis down", redis_down),
    "2": ("bad DB password after deploy", bad_db_password),
    "5": ("redis down + DB connection exhaustion", redis_down_and_db_exhaustion),
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
