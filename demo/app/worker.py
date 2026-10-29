"""Demo worker: pops jobs from Redis, writes them to Postgres."""
import logging
import os
import signal
import sys
import time

import psycopg
import redis

from main import cfg, dsn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")
cache = redis.Redis(host=cfg["REDIS_HOST"], socket_timeout=5, socket_connect_timeout=1)

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # PID 1 ignores SIGTERM otherwise → slow restarts

LEAK_FLAG = "/tmp/leak"  # chaos scenario 3 creates it; a fresh process starts clean
if os.path.exists(LEAK_FLAG):
    os.remove(LEAK_FLAG)
leaked, last_leak = [], time.time()

log.info("worker started")
conn = None
done = 0
while True:
    if os.path.exists(LEAK_FLAG) and time.time() - last_leak >= 1:
        leaked.append(b"x" * 8 * 2**20)  # simulated leak: an unbounded cache that never evicts, 8MB/s
        last_leak = time.time()
    try:
        conn = conn or psycopg.connect(dsn, autocommit=True)
        item = cache.brpop("jobs", timeout=2)
        if item:
            conn.execute("INSERT INTO jobs (created_at) VALUES (to_timestamp(%s))", (float(item[1]),))
            done += 1
            if done % 100 == 0:
                log.info("processed %d jobs", done)
    except Exception as e:
        log.error("worker error: %s", e)
        conn = None
        time.sleep(2)
