"""Demo worker: pops jobs from Redis, writes them to Postgres."""
import logging
import time

import psycopg
import redis

from main import cfg, dsn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")
cache = redis.Redis(host=cfg["REDIS_HOST"], socket_timeout=5, socket_connect_timeout=1)

log.info("worker started")
conn = None
done = 0
while True:
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
