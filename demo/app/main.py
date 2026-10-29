"""Demo API: /work touches Redis + Postgres, /health checks both, /metrics exposes counters."""
import logging
import os
import time

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
pool = ConnectionPool(dsn, min_size=1, max_size=int(cfg.get("DB_POOL_SIZE", 5)), timeout=2, open=False)
cache = redis.Redis(host=cfg["REDIS_HOST"], socket_timeout=1, socket_connect_timeout=1)
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
    stats["latency_ms_sum"] += (time.perf_counter() - t) * 1000
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
    n = stats["requests"] or 1
    p = pool.get_stats()
    return stats | {
        "error_rate": round(stats["errors"] / n, 3),
        "avg_latency_ms": round(stats["latency_ms_sum"] / n, 1),
        "pool_size": p.get("pool_size", 0), "pool_available": p.get("pool_available", 0),
        "pool_waiting": p.get("requests_waiting", 0),
    }
