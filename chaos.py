"""Inject faults into the demo system. Usage: uv run chaos.py {1|2|reset|list}"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEMO = Path(__file__).parent / "demo"
CFG = DEMO / "config" / "api" / "app.env"  # the api's own config (the worker has a separate copy)
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


# ---------- held-out scenarios 7 and 8 ----------
# Designed and implemented by a separate agent that never saw the prompts, runbooks or incident types
# (only demo/). Integrated unchanged except for paths. No prompt or runbook was tuned for them.
WORKER_CFG = DEMO / "config" / "worker" / "app.env"
WORKER_PREV = WORKER_CFG.with_suffix(".env.prev7")
MIGRATION = "migrate_0042_add_jobs_priority"


def _run(*args, check=True):
    r = subprocess.run(list(args), capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{args} -> rc={r.returncode}: {r.stderr.strip()}")
    return r


def _db_psql(sql):
    return _run("docker", "exec", "demo-db-1", "psql", "-U", "postgres", "-d", "app", "-tAc", sql).stdout.strip()


def worker_readonly_creds():
    """HELD-OUT 7: a worker config deploy ships the read-only `reporting` DB credentials. Login works, every
    INSERT fails with `permission denied for table jobs`, jobs are dropped and the Redis backlog grows.
    The api stays healthy."""
    if not WORKER_PREV.exists():  # never overwrite a good backup with an already-broken file
        shutil.copy2(WORKER_CFG, WORKER_PREV)
    text = WORKER_PREV.read_text().replace("DB_USER=app\n", "DB_USER=reporting\n") \
        .replace("DB_PASSWORD=app\n", "DB_PASSWORD=reporting\n")
    assert "DB_USER=reporting" in text and "DB_PASSWORD=reporting" in text, "unexpected worker config layout"
    WORKER_CFG.write_text(text)
    compose("restart", "-t", "5", "worker")  # config is read at process start, like a deploy rollout


def reset_7():
    changed = False
    if WORKER_PREV.exists():
        good = WORKER_PREV.read_bytes()
        changed = WORKER_CFG.read_bytes() != good
        if changed:
            shutil.copy2(WORKER_PREV, WORKER_CFG)
        WORKER_PREV.unlink()
    running = _run("docker", "inspect", "-f", "{{.State.Running}}", "demo-worker-1", check=False).stdout.strip() == "true"
    if changed and running:
        compose("restart", "-t", "5", "worker")
    elif not running:
        compose("up", "-d", "--no-deps", "worker")


def stuck_migration_lock():
    """HELD-OUT 8: a hand-run migration (`BEGIN; ALTER TABLE jobs ...`) is left idle in transaction holding an
    ACCESS EXCLUSIVE lock. api queries block, its pool fills, /health fails; db itself stays healthy."""
    reset_8()  # never stack two lock holders
    script = (f"(echo \"BEGIN; ALTER TABLE jobs ADD COLUMN priority int DEFAULT 0;\"; sleep 86400) | "
              f"PGAPPNAME={MIGRATION} PGPASSWORD=app psql -h 127.0.0.1 -U app -d app -q")
    _run("docker", "exec", "-d", "demo-db-1", "sh", "-c", script)
    for _ in range(20):  # wait until the lock is actually held
        if _db_psql(f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{MIGRATION}' "
                    "AND state='idle in transaction'") == "1":
            _user_burst()
            return
        time.sleep(0.5)
    raise RuntimeError("scenario 8: lock holder did not start")


def _user_burst(n=8):
    """The demo's single sequential curl just hangs on its first blocked request (an empty metrics window).
    Real traffic is concurrent: a short burst of users (3s client timeout) fills the pool, as production would."""
    import threading
    import urllib.request

    def hit():
        try:
            urllib.request.urlopen("http://localhost:8000/work", timeout=3).read()
        except Exception:
            pass
    ts = [threading.Thread(target=hit) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


def reset_8():
    if _run("docker", "inspect", "-f", "{{.State.Running}}", "demo-db-1", check=False).stdout.strip() != "true":
        compose("up", "-d", "--no-deps", "db")
        return
    # terminating the backend rolls back the ALTER, so the schema is exactly as before
    _db_psql(f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE application_name='{MIGRATION}'")
    for pat in (MIGRATION, "sleep 86400"):  # the sh/sleep feeding the stranded psql
        _run("docker", "exec", "demo-db-1", "pkill", "-f", pat, check=False)


def reset():
    subprocess.run(["docker", "rm", "-f", LEAK], capture_output=True)
    reset_8()
    reset_7()
    if PREV.exists():
        shutil.move(PREV, CFG)
    for attempt in range(6):  # compose up waits for db healthy; after a failed run db can need a while
        try:
            compose("up", "-d", "--scale", "worker=1")
            break
        except subprocess.CalledProcessError as e:
            if attempt == 5:
                raise RuntimeError(f"reset failed: {e.stderr.decode()[-300:]}") from e
            time.sleep(10)
    compose("restart", "api", "worker")  # also clears the api tmpfs and the worker leak


SCENARIOS = {
    "0": ("no fault (control): healthy system, normal traffic", lambda: None),
    "1": ("redis down", redis_down),
    "2": ("bad DB password after deploy", bad_db_password),
    "3": ("worker memory leak", worker_memory_leak),
    "4": ("disk fill on api scratch volume", disk_fill),
    "5": ("redis down + DB connection exhaustion", redis_down_and_db_exhaustion),
    "6": ("HELD-OUT: wrong REDIS_HOST in api config after deploy", bad_redis_host),
    "7": ("HELD-OUT: worker deployed with read-only DB credentials", worker_readonly_creds),
    "8": ("HELD-OUT: forgotten migration holding a table lock", stuck_migration_lock),
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
    "7": {("*", "worker")},  # held out, designed by a separate agent: graded on the root service
    "8": {("*", "db")},
}


# Actions that are a correct, proportionate fix. Anything else (or a repeat) counts as unnecessary.
# Deliberately strict: e.g. restart(db) also clears scenario 5's connections but is disruptive on a stateful
# service; restart(api) also empties scenario 4's tmpfs but throws away everything else on it.
EXPECTED_ACTIONS = {
    "0": set(),
    "1": {("restart", "cache")},
    "2": {("rollback_config", "api")},
    "3": {("restart", "worker"), ("scale", "worker")},
    "4": {("clear_disk", "api")},
    "5": {("restart", "cache"), ("kill_idle_connections", "db")},
    "6": {("rollback_config", "api")},
    "7": {("rollback_config", "worker"), ("restart", "worker")},
    "8": {("kill_idle_connections", "db")},  # restart(db) works but is disproportionate (designer's ground truth)
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
