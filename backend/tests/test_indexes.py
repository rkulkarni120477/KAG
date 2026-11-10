"""Index adapter integration tests against real Qdrant and Neo4j containers.

Skipped unless the services are configured, e.g.:
  QDRANT_URL=http://localhost:6333 NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... pytest tests/test_indexes.py
Mocks cannot establish query filtering or index consistency, so these tests must run against
the real containers before a release.
"""
import os
import tempfile
from uuid import uuid4

os.environ.setdefault("KAG_DATA_DIR", tempfile.mkdtemp(prefix="kag-indexes-test-"))
os.environ["KAG_LOAD_DOTENV"] = "false"
os.environ["WEB_SEARCH_ENABLED"] = "false"

import pytest

from app.db import initialize
from app.indexes import QDRANT_COLLECTION

qdrant_configured = pytest.mark.skipif(not os.getenv("QDRANT_URL"), reason="QDRANT_URL not set")
neo4j_configured = pytest.mark.skipif(not os.getenv("NEO4J_URI"), reason="NEO4J_URI not set")


@pytest.fixture(autouse=True)
def schema():
    initialize()


@qdrant_configured
def test_qdrant_is_tenant_isolated_and_generation_gated():
    from app.indexes import QdrantVectorIndex

    index = QdrantVectorIndex()
    tenant, other = f"it-{uuid4().hex[:8]}", f"it-{uuid4().hex[:8]}"
    document = {"id": f"res-{uuid4().hex[:12]}", "title": "Prime numbers", "text": "Primes have exactly two divisors. Integration text."}
    index.upsert_resources(tenant, 5, [document])
    index.upsert_resources(tenant, 5, [document])  # replay must not duplicate the point
    candidates = [document]
    assert [rid for rid, _ in index.rank(tenant, 5, "prime numbers divisors", candidates)] == [document["id"]]
    assert index.rank(other, 5, "prime numbers divisors", candidates) == []
    assert index.rank(tenant, 4, "prime numbers divisors", candidates) == []  # written at a newer generation
    count = index.client.count(
        collection_name=QDRANT_COLLECTION,
        count_filter=index.models.Filter(must=[index.models.FieldCondition(key="tenant_id", match=index.models.MatchValue(value=tenant))]),
        exact=True,
    ).count
    assert count == 1


@qdrant_configured
def test_qdrant_candidates_must_be_published_resources():
    from app.indexes import QdrantVectorIndex

    index = QdrantVectorIndex()
    tenant = f"it-{uuid4().hex[:8]}"
    published = {"id": f"res-{uuid4().hex[:12]}", "title": "Fractions", "text": "Fractions name parts of a whole. Integration text."}
    unpublished = {"id": f"res-{uuid4().hex[:12]}", "title": "Fractions draft", "text": "Fractions draft written by a failed publication."}
    index.upsert_resources(tenant, 1, [published, unpublished])
    ranked = [rid for rid, _ in index.rank(tenant, 1, "fractions parts whole", [published])]
    assert ranked == [published["id"]]


@neo4j_configured
def test_neo4j_is_tenant_isolated_idempotent_and_generation_gated():
    from app.indexes import Neo4jGraphIndex

    index = Neo4jGraphIndex()
    tenant, other = f"it-{uuid4().hex[:8]}", f"it-{uuid4().hex[:8]}"
    pair = (f"res-{uuid4().hex[:12]}", "ratios")
    try:
        index.upsert_support(tenant, 3, [pair])
        index.upsert_support(tenant, 3, [pair])
        assert index.support_pairs(tenant, 3) == {pair}
        assert index.support_pairs(tenant, 2) == set()
        assert index.support_pairs(other, 3) == set()
    finally:
        index.driver.execute_query("MATCH (n) WHERE n.tenant_id IN [$a, $b] DETACH DELETE n", a=tenant, b=other)


@neo4j_configured
def test_publish_then_retrieve_uses_the_configured_backends():
    from app.ingestion import publish
    from app.retrieval import retrieve

    tenant = f"it-{uuid4().hex[:8]}"
    publish(tenant, [{"title": "Lunar phases", "text": "The Moon shows phases as sunlight changes its visible side. Integration text."}])
    results = retrieve(tenant, "moon phases sunlight")
    assert results and results[0]["title"] == "Lunar phases"
