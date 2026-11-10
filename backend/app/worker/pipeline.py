"""Ingestion pipeline stages. Runs in the worker process (or inline when no queue is configured).

  prepare  (source)  validate, normalize, content-address, link entities, report new vs existing
  chunk    (worker)  split on sentence boundaries, with overlap
  embed    (worker)  vectors from the configured embedder (skipped for the offline vector backend)
  index    (worker)  vector chunks and graph support edges tagged with the target generation
  commit   (source)  compare-and-swap the generation and insert rows in one transaction

Failure before commit leaves only index points that no query can reach. Repeated replays converge.
"""
import logging

from ..domain.documents import chunk_id, chunk_text
from ..embeddings import embedder
from ..indexes import graph_index, vector_index
from ..source_client import source

log = logging.getLogger("kag.worker")
MAX_PUBLISH_ATTEMPTS = 3


class PublicationConflict(RuntimeError):
    pass


def build_chunks(pending: list[dict]) -> list[dict]:
    chunks = []
    for doc in pending:
        pieces = chunk_text(doc["title"] + ". " + doc["text"]) or [doc["text"]]
        for ordinal, piece in enumerate(pieces):
            chunks.append({"id": chunk_id(doc["id"], ordinal), "resource_id": doc["id"], "ordinal": ordinal, "text": piece})
    return chunks


def _index_chunks(tenant: str, generation: int, chunks: list[dict]) -> None:
    index = vector_index()
    if index.name == "sqlite":
        return  # offline backend recomputes vectors from published text at query time
    vectors = embedder().embed_documents([c["text"] for c in chunks])
    index.index_chunks(tenant, generation, [
        {"id": c["id"], "resource_id": c["resource_id"], "ordinal": c["ordinal"], "vector": v} for c, v in zip(chunks, vectors)
    ])


def publish_documents(tenant: str, documents: list[dict], topic_label: str | None = None) -> dict:
    """Index and publish documents for one tenant. Returns resource IDs and whether anything changed."""
    client = source()
    for _ in range(MAX_PUBLISH_ATTEMPTS):
        prep = client.publish_prepare(tenant, documents)
        current, prepared = prep["generation"], prep["prepared"]
        pending = [d for d in prepared if d["new"]]
        ids = [d["id"] for d in prepared]
        if not pending:
            return {"resource_ids": ids, "generation": current, "changed": False}
        target = current + 1
        _index_chunks(tenant, target, build_chunks(pending))
        graph_index().upsert_support(tenant, target, [(d["id"], e) for d in pending for e in d["entities"]])
        if client.publish_commit(tenant, current, target, prepared, topic_label):
            return {"resource_ids": ids, "generation": target, "changed": True}
        log.warning("Publication conflict; retrying with a fresh generation")
    raise PublicationConflict("Publication failed after retries; no partial generation was published")


def purge_web(tenant: str, days: int) -> dict:
    """Remove expired web-cache rows. SQLite commits first; index entries are removed afterwards."""
    result = source().purge_web(tenant, days)
    ids = result["ids"]
    if ids:
        vector_index().delete_resources(tenant, ids)
        graph_index().delete_resources(tenant, ids)
        log.info("Purged %d cached web result(s)", len(ids))
    return {"deleted": len(ids), "generation": result["generation"]}


def rebuild_indexes() -> dict:
    """Rebuild derived indexes for every tenant from published rows. Idempotent; no-op offline."""
    vector, graph = vector_index(), graph_index()
    rebuilt = 0
    if vector.name == "sqlite" and graph.name == "sqlite":
        return {"tenants": 0}
    client = source()
    for tenant in client.tenants():
        docs = client.index_documents(tenant)
        generation = docs["generation"]
        chunks = build_chunks(docs["documents"])
        if vector.name != "sqlite":
            _index_chunks(tenant, generation, chunks)
        graph.upsert_support(tenant, generation, [tuple(p) for p in docs["support"]])
        rebuilt += 1
    log.info("Rebuilt derived indexes for %d tenant(s)", rebuilt)
    return {"tenants": rebuilt}
