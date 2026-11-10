"""Redis-backed runtime services, each with an in-process fallback when REDIS_URL is not set.

Redis is used for:
  1. the ingestion and maintenance job queue (RQ), consumed by the worker service;
  2. fixed-window rate limiting for the API;
  3. login-failure counters (lockout after repeated failures);
  4. a query-answer cache keyed by tenant, published generation and request, so a new publication
     invalidates earlier answers automatically and tenants never share entries.

KAG_QUEUE_MODE=inline runs jobs in the request (development without Redis);
KAG_QUEUE_MODE=redis enqueues to RQ. KAG_QUEUE_ASYNC=false executes enqueued jobs immediately in the
calling process, which tests use with an in-memory Redis.
"""
import hashlib
import json
import os
import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_redis = None
_memory_windows: dict = defaultdict(deque)
_memory_cache: dict = {}
_memory_counters: dict = defaultdict(lambda: [0, 0.0])


def set_redis(connection) -> None:
    """Install a Redis connection (tests use fakeredis). Pass None to reset."""
    global _redis
    with _lock:
        _redis = connection


def redis_conn():
    global _redis
    with _lock:
        if _redis is None and os.getenv("REDIS_URL"):
            import redis

            _redis = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2, socket_connect_timeout=2)
        return _redis


def queue_mode() -> str:
    return os.getenv("KAG_QUEUE_MODE", "inline").lower()


def enqueue(func, *args, **kwargs):
    """Run a job through RQ when configured, otherwise inline. Returns the RQ job or None."""
    connection = redis_conn()
    if queue_mode() != "redis" or connection is None:
        func(*args, **kwargs)
        return None
    from rq import Queue

    queue = Queue("kag", connection=connection, is_async=os.getenv("KAG_QUEUE_ASYNC", "true").lower() != "false")
    return queue.enqueue(func, *args, **kwargs, job_timeout=600, result_ttl=3600)


def allow(bucket: str, limit: int, window_seconds: int = 60) -> bool:
    """Fixed-window rate limit. Returns False when the caller is over the limit for this window."""
    window = int(time.time() // window_seconds)
    connection = redis_conn()
    if connection is not None:
        key = f"kag:rl:{bucket}:{window}"
        count = connection.incr(key)
        if count == 1:
            connection.expire(key, window_seconds + 5)
        return count <= limit
    now = time.monotonic()
    history = _memory_windows[bucket]
    while history and history[0] < now - window_seconds:
        history.popleft()
    if len(history) >= limit:
        return False
    history.append(now)
    return True


def failure_count(key: str) -> int:
    connection = redis_conn()
    if connection is not None:
        value = connection.get(f"kag:fail:{key}")
        return int(value) if value else 0
    count, expires = _memory_counters[key]
    return count if expires > time.monotonic() else 0


def record_failure(key: str, window_seconds: int = 900) -> None:
    connection = redis_conn()
    if connection is not None:
        name = f"kag:fail:{key}"
        connection.incr(name)
        connection.expire(name, window_seconds)
        return
    count, expires = _memory_counters[key]
    if expires <= time.monotonic():
        count = 0
    _memory_counters[key] = [count + 1, time.monotonic() + window_seconds]


def clear_failures(key: str) -> None:
    connection = redis_conn()
    if connection is not None:
        connection.delete(f"kag:fail:{key}")
    _memory_counters.pop(key, None)


def cache_key(*parts) -> str:
    return "kag:qa:" + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def cache_get(key: str):
    connection = redis_conn()
    if connection is not None:
        raw = connection.get(key)
        return json.loads(raw) if raw else None
    entry = _memory_cache.get(key)
    if not entry or entry[1] < time.monotonic():
        return None
    return entry[0]


def cache_put(key: str, value, ttl_seconds: int = 300) -> None:
    connection = redis_conn()
    if connection is not None:
        connection.set(key, json.dumps(value), ex=ttl_seconds)
        return
    _memory_cache[key] = (value, time.monotonic() + ttl_seconds)
