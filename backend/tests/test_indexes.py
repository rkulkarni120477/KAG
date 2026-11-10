"""Index adapter tests against real Qdrant and Neo4j containers. Skipped unless the services are configured.

  QDRANT_URL=http://localhost:6333 NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... pytest tests/test_indexes.py

Mocks cannot establish query filtering or index consistency, so these must run against real containers.
"""
import os
import uuid

import pytest

from app.embeddings import HashedEmbedder

qdrant_configured = pytest.mark.skipif(not os.getenv("QDRANT_URL"), reason="QDRANT_URL not set")
neo4j_configured = pytest.mark.skipif(not os.getenv("NEO4J_URI"), reason="NEO4J_URI not set")
EMBED = HashedEmbedder()


def _chunk(resource_id, text, ordinal=0):
    return {"id": f"{resource_id}#{ordinal}", "resource_id": resource_id, "ordinal": ordinal, "vector": EMBED.embed_documents([text])[0]}


@qdrant_configured
def test_qdrant_is_tenant_isolated_and_generation_gated():
    from app.indexes import QdrantVectorIndex

    index = QdrantVectorIndex()
    tenant, other = f"it-{uuid.uuid4().hex[:8]}", f"it-{uuid.uuid4().hex[:8]}"
    rid = f"res-{uuid.uuid4().hex[:12]}"
    doc = {"id": rid, "title": "Prime numbers", "text": "Primes have exactly two divisors."}
    chunk = _chunk(rid, "Prime numbers. Primes have exactly two divisors.")
    index.index_chunks(tenant, 5, [chunk])
    index.index_chunks(tenant, 5, [chunk])  # replay must not duplicate the point
    assert [r for r, _ in index.rank(tenant, 5, "prime numbers divisors", [doc])] == [rid]
    assert index.rank(other, 5, "prime numbers divisors", [doc]) == []
    assert index.rank(tenant, 4, "prime numbers divisors", [doc]) == []  # written at a newer generation
    count = index.client.count(collection_name=index.collection, exact=True, count_filter=index.models.Filter(
        must=[index.models.FieldCondition(key="tenant_id", match=index.models.MatchValue(value=tenant))])).count
    assert count == 1


@qdrant_configured
def test_qdrant_candidates_must_be_published_resources():
    from app.indexes import QdrantVectorIndex

    index = QdrantVectorIndex()
    tenant = f"it-{uuid.uuid4().hex[:8]}"
    published = f"res-{uuid.uuid4().hex[:12]}"
    unpublished = f"res-{uuid.uuid4().hex[:12]}"
    index.index_chunks(tenant, 1, [
        _chunk(published, "Fractions name parts of a whole."),
        _chunk(unpublished, "Fractions draft written by a failed publication."),
    ])
    ranked = [r for r, _ in index.rank(tenant, 1, "fractions parts whole", [{"id": published, "title": "F", "text": "x"}])]
    assert ranked == [published]


@qdrant_configured
def test_qdrant_deletion_removes_only_the_named_resources():
    from app.indexes import QdrantVectorIndex

    index = QdrantVectorIndex()
    tenant = f"it-{uuid.uuid4().hex[:8]}"
    keep, drop = f"res-{uuid.uuid4().hex[:12]}", f"res-{uuid.uuid4().hex[:12]}"
    index.index_chunks(tenant, 1, [_chunk(keep, "This resource stays published."), _chunk(drop, "This resource is purged.")])
    index.delete_resources(tenant, [drop])
    candidates = [{"id": keep, "title": "k", "text": "k"}, {"id": drop, "title": "d", "text": "d"}]
    assert {r for r, _ in index.rank(tenant, 1, "resource published purged", candidates)} == {keep}


@qdrant_configured
def test_qdrant_scores_each_resource_once_across_its_chunks():
    from app.indexes import QdrantVectorIndex

    index = QdrantVectorIndex()
    tenant = f"it-{uuid.uuid4().hex[:8]}"
    rid = f"res-{uuid.uuid4().hex[:12]}"
    index.index_chunks(tenant, 1, [_chunk(rid, "Tides rise and fall.", 0), _chunk(rid, "Moon phases follow the lunar cycle.", 1)])
    ranked = index.rank(tenant, 1, "moon phases lunar", [{"id": rid, "title": "t", "text": "t"}])
    assert [r for r, _ in ranked] == [rid]


@neo4j_configured
def test_neo4j_is_tenant_isolated_idempotent_and_generation_gated():
    from app.indexes import Neo4jGraphIndex

    index = Neo4jGraphIndex()
    tenant, other = f"it-{uuid.uuid4().hex[:8]}", f"it-{uuid.uuid4().hex[:8]}"
    pair = (f"res-{uuid.uuid4().hex[:12]}", "ratios")
    try:
        index.upsert_support(tenant, 3, [pair])
        index.upsert_support(tenant, 3, [pair])
        assert index.support_pairs(tenant, 3) == {pair}
        assert index.support_pairs(tenant, 2) == set()
        assert index.support_pairs(other, 3) == set()
    finally:
        index.driver.execute_query("MATCH (n) WHERE n.tenant_id IN [$a, $b] DETACH DELETE n", a=tenant, b=other)


@neo4j_configured
def test_neo4j_deletion_detaches_resources():
    from app.indexes import Neo4jGraphIndex

    index = Neo4jGraphIndex()
    tenant = f"it-{uuid.uuid4().hex[:8]}"
    rid = f"res-{uuid.uuid4().hex[:12]}"
    try:
        index.upsert_support(tenant, 1, [(rid, "ratios")])
        index.delete_resources(tenant, [rid])
        assert index.support_pairs(tenant, 1) == set()
    finally:
        index.driver.execute_query("MATCH (n) WHERE n.tenant_id = $t DETACH DELETE n", t=tenant)


@neo4j_configured
@qdrant_configured
def test_publish_then_retrieve_uses_the_configured_backends():
    from app.retrieval import retrieve
    from app.worker.pipeline import publish_documents

    tenant = f"it-{uuid.uuid4().hex[:8]}"
    publish_documents(tenant, [{"title": "Lunar phases", "text": "The Moon shows phases as sunlight changes its visible side. Integration text."}])
    results = retrieve(tenant, "moon phases sunlight")
    assert results and results[0]["title"] == "Lunar phases"
