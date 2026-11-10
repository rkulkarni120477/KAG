"""Queued tasks. Functions here are referenced by import path from RQ, so keep them module-level."""
import logging

from ..runtime import enqueue
from ..source_client import source
from .pipeline import purge_web, publish_documents

log = logging.getLogger("kag.worker")


def ingest_job(tenant: str, job_id: str) -> None:
    """Run one queued ingestion job end to end and record its outcome in the source service."""
    client = source()
    job = client.job(tenant, job_id, with_payload=True)
    if not job or job["status"] != "queued":
        return
    try:
        result = publish_documents(tenant, [job["payload"]])
        client.finish_job(tenant, job_id, "completed", resource_id=result["resource_ids"][0])
        client.add_audit(tenant, job["user_id"], "ingestion_completed",
                         {"job_id": job_id, "published_generation": result["generation"], "changed": result["changed"]})
    except Exception:
        log.exception("Ingestion job failed; no partial generation was published")
        client.finish_job(tenant, job_id, "failed", error="Import failed validation or storage; retry with a new key.")


def publish_web(tenant: str, documents: list[dict], topic_label: str | None) -> None:
    publish_documents(tenant, documents, topic_label)


def purge(tenant: str, days: int) -> None:
    purge_web(tenant, days)


def purge_all(days: int) -> None:
    for tenant in source().tenants():
        purge_web(tenant, days)


def submit_ingest(tenant: str, job_id: str):
    return enqueue(ingest_job, tenant, job_id)


def submit_web_publish(tenant: str, documents: list[dict], topic_label: str | None):
    return enqueue(publish_web, tenant, documents, topic_label)
