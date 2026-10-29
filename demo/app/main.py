"""Demo API: /work touches Redis + Postgres, /health checks both, /metrics exposes counters."""
import logging
import os
import time
from collections import deque

import redis
from fastapi import FastAPI, Response
from psycopg_pool import ConnectionPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


def load_config(path="/config/app.env"):
    """Config file is mounted so chaos can simulate a bad deploy."""
    cfg = {}
    with open(path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                cfg[k] = v
    return cfg


cfg = load_config()
dsn = f"host={cfg['DB_HOST']} dbname={cfg['DB_NAME']} user={cfg['DB_USER']} password={cfg['DB_PASSWORD']} connect_timeout=2"
# max_lifetime: connections are recycled, so the pool needs fresh DB connections regularly (like a real app)
pool = ConnectionPool(dsn, min_size=1, max_size=int(cfg.get("DB_POOL_SIZE", 5)), timeout=2, max_lifetime=20,
                      reconnect_timeout=3600, open=False)
cache = redis.Redis(host=cfg["REDIS_HOST"], socket_timeout=1, socket_connect_timeout=1)
WINDOW_S = 10
recent = deque()  # (ts, ok, latency_ms) for the last WINDOW_S seconds
stats = {"requests": 0, "errors": 0, "db_errors": 0, "cache_errors": 0, "latency_ms_sum": 0.0, "started": time.time()}

app = FastAPI()


@app.on_event("startup")
def startup():
    pool.open(wait=False)
    log.info("api started, db_host=%s pool_size=%s", cfg["DB_HOST"], pool.max_size)


@app.get("/work")
def work(response: Response):
    t = time.perf_counter()
    stats["requests"] += 1
    ok = True
    try:
        cache.incr("hits")
        cache.lpush("jobs", str(time.time()))
    except Exception as e:
        ok = False
        stats["cache_errors"] += 1
        log.error("cache error: %s", e)
    try:
        with pool.connection() as conn:
            conn.execute("SELECT count(*) FROM jobs").fetchone()
    except Exception as e:
        ok = False
        stats["db_errors"] += 1
        log.error("db error: %s", e)
    ms = (time.perf_counter() - t) * 1000
    stats["latency_ms_sum"] += ms
    recent.append((time.time(), ok, ms))
    if not ok:
        stats["errors"] += 1
        response.status_code = 503
    return {"ok": ok}


@app.get("/health")
def health(response: Response):
    checks = {}
    try:
        cache.ping()
        checks["cache"] = "ok"
    except Exception as e:
        checks["cache"] = f"fail: {e}"
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"fail: {e}"
    healthy = all(v == "ok" for v in checks.values())
    response.status_code = 200 if healthy else 503
    return {"healthy": healthy, "checks": checks}


@app.get("/metrics")
def metrics():
    """error_rate / avg_latency_ms are over the last WINDOW_S seconds so they recover after a fix."""
    while recent and recent[0][0] < time.time() - WINDOW_S:
        recent.popleft()
    n = len(recent) or 1
    p = pool.get_stats()
    return stats | {
        "window_s": WINDOW_S, "window_requests": len(recent),
        "error_rate": round(sum(not ok for _, ok, _ in recent) / n, 3),
        "avg_latency_ms": round(sum(ms for _, _, ms in recent) / n, 1),
        "pool_size": p.get("pool_size", 0), "pool_available": p.get("pool_available", 0),
        "pool_waiting": p.get("requests_waiting", 0),
    }
