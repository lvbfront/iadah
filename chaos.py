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


def _bad_deploy(key: str, value: str):
    """A deploy ships one wrong config value to the api (previous config kept for reset)."""
    shutil.copy(CFG, PREV)
    text = "\n".join(f"{key}={value}" if l.startswith(f"{key}=") else l for l in CFG.read_text().splitlines())
    CFG.write_text(text + "\n")
    compose("restart", "api")


def bad_db_password():
    _bad_deploy("DB_PASSWORD", "wrong-after-deploy")


def bad_redis_host():
    """HELD-OUT scenario: never used to tune prompts or runbooks."""
    _bad_deploy("REDIS_HOST", "cache-v2")


def worker_memory_leak():
    """Flips the worker's leak flag; it then grows 8MB/s toward its 256MB limit."""
    subprocess.run(["docker", "exec", "demo-worker-1", "touch", "/tmp/leak"], check=True, capture_output=True)


def disk_fill():
    """A runaway export fills the api's 16MB scratch volume; audit-log writes then fail with ENOSPC."""
    subprocess.run(["docker", "exec", "demo-api-1", "sh", "-c",
                    "dd if=/dev/zero of=/data/tmp/export-$(date +%s).tmp bs=1M count=32"], capture_output=True)


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
    compose("up", "-d", "--scale", "worker=1")
    compose("restart", "api", "worker")  # also clears the api tmpfs and the worker leak


SCENARIOS = {
    "0": ("no fault (control): healthy system, normal traffic", lambda: None),
    "1": ("redis down", redis_down),
    "2": ("bad DB password after deploy", bad_db_password),
    "3": ("worker memory leak", worker_memory_leak),
    "4": ("disk fill on api scratch volume", disk_fill),
    "5": ("redis down + DB connection exhaustion", redis_down_and_db_exhaustion),
    "6": ("HELD-OUT: wrong REDIS_HOST in api config after deploy", bad_redis_host),
    "reset": ("restore healthy state", reset),
}

# Ground truth root causes per scenario: {(incident_type, service)} — used by benchmarks/run.py.
# "*" = any incident type: scenario 6's fault (a wrong cache hostname in the api's config) has no matching
# type in the vocabulary, and we deliberately did not add one. It is graded on locating the root at the api
# (the cache itself is healthy; any real fix must target the api's config).
TRUTH = {
    "0": set(),  # control: nothing should be detected or touched
    "1": {("cache_down", "cache")},
    "2": {("db_auth_failure", "api")},
    "3": {("memory_leak", "worker")},
    "4": {("disk_full", "api")},
    "5": {("cache_down", "cache"), ("db_pool_exhaustion", "db")},
    "6": {("*", "api")},
}


def diag_correct(scenario: str, roots: set[tuple[str, str]]) -> bool:
    truth = TRUTH[scenario]
    if len(roots) != len(truth):
        return False
    return all(any(s == ts and (tt == "*" or t == tt) for tt, ts in truth) for t, s in roots)

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "list"
    if arg not in SCENARIOS:
        for k, (desc, _) in SCENARIOS.items():
            print(f"  {k:6} {desc}")
        sys.exit(0 if arg == "list" else 1)
    print(f"chaos: {SCENARIOS[arg][0]}")
    SCENARIOS[arg][1]()
