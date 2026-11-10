"""Worker entry points.

  python -m app.worker run        rebuild derived indexes, then consume the "kag" queue
  python -m app.worker scheduler  enqueue the web-cache purge on a fixed interval
"""
import logging
import os
import sys
import time

from ..runtime import enqueue, redis_conn
from .pipeline import rebuild_indexes
from .tasks import purge_all

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def run() -> None:
    connection = redis_conn()
    if connection is None:
        sys.exit("REDIS_URL is required for the worker")
    rebuild_indexes()
    from rq import Queue, Worker

    worker = Worker([Queue("kag", connection=connection)], connection=connection)
    worker.work(with_scheduler=False)


def scheduler() -> None:
    interval = int(os.getenv("WEB_PURGE_INTERVAL_HOURS", "6")) * 3600
    days = int(os.getenv("WEB_CACHE_DAYS", "30"))
    while True:
        enqueue(purge_all, days)
        time.sleep(interval)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "run":
        run()
    elif command == "scheduler":
        scheduler()
    else:
        sys.exit("usage: python -m app.worker run|scheduler")
