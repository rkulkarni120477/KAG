"""Redis-backed runtime: rate limiting, lockout counters, the query cache and the RQ job queue.

Uses fakeredis so the Redis code path runs without a server. RQ runs jobs synchronously (KAG_QUEUE_ASYNC=false),
which exercises enqueue, job arguments and the task functions without a worker process.
"""
import os
import uuid

import fakeredis
import pytest

from app import runtime
from app.worker import tasks
from helpers import query_one, sql


@pytest.fixture
def redis_backend(monkeypatch):
    connection = fakeredis.FakeRedis()
    runtime.set_redis(connection)
    monkeypatch.setenv("KAG_QUEUE_MODE", "redis")
    monkeypatch.setenv("KAG_QUEUE_ASYNC", "false")
    yield connection
    runtime.set_redis(None)


def test_rate_limit_blocks_after_the_limit_in_the_window(redis_backend):
    bucket = f"test-{uuid.uuid4().hex}"
    assert [runtime.allow(bucket, 3) for _ in range(4)] == [True, True, True, False]
    assert any(key.decode().startswith("kag:rl:" + bucket) for key in redis_backend.keys())


def test_rate_limit_counters_expire(redis_backend):
    bucket = f"expire-{uuid.uuid4().hex}"
    runtime.allow(bucket, 5, window_seconds=60)
    keys = [k for k in redis_backend.keys() if k.decode().startswith("kag:rl:" + bucket)]
    assert keys and all(redis_backend.ttl(k) > 0 for k in keys)


def test_failed_login_counter_is_shared_and_cleared(redis_backend):
    key = f"user-{uuid.uuid4().hex}@lab.demo"
    for _ in range(3):
        runtime.record_failure(key)
    assert runtime.failure_count(key) == 3
    runtime.clear_failures(key)
    assert runtime.failure_count(key) == 0


def test_query_cache_is_keyed_by_tenant_generation_and_question(redis_backend):
    base = runtime.cache_key("northstar", 4, "alex", "How do I prepare?")
    assert base != runtime.cache_key("other", 4, "alex", "How do I prepare?")
    assert base != runtime.cache_key("northstar", 5, "alex", "How do I prepare?")  # new publication invalidates
    assert base != runtime.cache_key("northstar", 4, "jamie", "How do I prepare?")
    runtime.cache_put(base, {"answer": "cached"}, ttl_seconds=60)
    assert runtime.cache_get(base) == {"answer": "cached"}
    assert runtime.cache_get(runtime.cache_key("northstar", 5, "alex", "How do I prepare?")) is None


def test_ingestion_runs_through_rq_and_completes(redis_backend):
    tenant = f"rq-{uuid.uuid4().hex[:8]}"
    sql("INSERT INTO institutions VALUES (?,?,?)", (tenant, "RQ lab", "DEMO"))
    from app.source_client import source
    status, job = source().create_job(tenant, "admin", f"rq-key-{uuid.uuid4().hex[:8]}",
                                      {"title": "Queued doc", "text": "A document that travels through the Redis queue. Synthetic.", "skill_id": None})
    assert status == 202 and job["created"] is True
    runtime.enqueue(tasks.ingest_job, tenant, job["id"])
    finished = source().job(tenant, job["id"])
    assert finished["status"] == "completed", finished
    assert query_one("SELECT COUNT(*) AS n FROM resources WHERE id=?", (finished["resource_id"],))["n"] == 1


def test_queue_failure_is_recorded_on_the_job_without_a_partial_publication(redis_backend):
    tenant = f"rq-fail-{uuid.uuid4().hex[:8]}"
    sql("INSERT INTO institutions VALUES (?,?,?)", (tenant, "RQ fail lab", "DEMO"))
    from app.source_client import source
    _, job = source().create_job(tenant, "admin", f"rq-fail-{uuid.uuid4().hex[:8]}",
                                 {"title": "Valid title", "text": "A valid document body that will be corrupted later.", "skill_id": None})
    # Corrupt the stored payload so the worker's validation fails after dequeue.
    sql("UPDATE ingestion_jobs SET payload=? WHERE id=?", ('{"title": "ab", "text": "tiny", "skill_id": null}', job["id"]))
    runtime.enqueue(tasks.ingest_job, tenant, job["id"])
    failed = source().job(tenant, job["id"])
    assert failed["status"] == "failed"
    assert query_one("SELECT COUNT(*) AS n FROM resources WHERE tenant_id=?", (tenant,))["n"] == 0


def test_inline_mode_runs_the_job_in_the_request(monkeypatch):
    monkeypatch.setenv("KAG_QUEUE_MODE", "inline")
    runtime.set_redis(None)
    calls = []
    runtime.enqueue(lambda *a: calls.append(a), 1, 2)
    assert calls == [(1, 2)]
