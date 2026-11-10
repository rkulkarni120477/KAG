"""Ingestion: idempotent convergence, atomic publication, visibility gating, retries and purge."""
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.documents import chunk_text, content_resource_id, validate
from app.retrieval import _search_local, retrieve
from app.source_client import SourceError, source
from app.worker import pipeline
from app.worker.pipeline import publish_documents, purge_web
from helpers import query_all, query_one, sql


def lab_tenant() -> str:
    tenant = f"lab-ingest-{uuid.uuid4().hex[:8]}"
    sql("INSERT INTO institutions VALUES (?,?,?)", (tenant, "Ingest lab", "DEMO"))
    return tenant


def document(title="Coastal ecosystems", text=None, **extra):
    return {"title": title, "text": text or "Tidal marshes filter sediment and shelter juvenile fish. Synthetic lab text.", **extra}


def generation(tenant):
    row = query_one("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant,))
    return row["generation"] if row else None


def resource_count(tenant):
    return query_one("SELECT COUNT(*) AS n FROM resources WHERE tenant_id=?", (tenant,))["n"]


class FailingIndex:
    name = "failing"

    def __init__(self, error):
        self.error = error

    def index_chunks(self, *args):
        raise self.error

    def upsert_resources(self, *args):
        raise self.error

    def upsert_support(self, *args):
        raise self.error

    def rank(self, *args):
        return []


class NoopGraph:
    name = "noop"

    def upsert_support(self, *args):
        return None

    def delete_resources(self, *args):
        return None


class ProxySource:
    """Wraps the real source client so tests can interpose on publish_commit."""

    def __init__(self, real, commit):
        self._real = real
        self._commit = commit

    def __getattr__(self, name):
        return getattr(self._real, name)

    def publish_commit(self, *args, **kwargs):
        return self._commit(self._real, *args, **kwargs)


def test_identical_content_converges_to_one_resource_and_no_generation_bump():
    tenant = lab_tenant()
    first = publish_documents(tenant, [document()])
    gen = generation(tenant)
    second = publish_documents(tenant, [document(text="  " + document()["text"] + "\n\n\n")])
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["resource_ids"] == first["resource_ids"]
    assert generation(tenant) == gen
    assert resource_count(tenant) == 1


def test_unicode_and_control_characters_normalize_to_the_same_identity():
    composed = "Café ecosystems. " + "Tidal marshes filter sediment and shelter fish in estuaries."
    decomposed = unicodedata.normalize("NFD", composed)
    with_control = composed.replace("Café", "Caf\x07é")
    ids = {content_resource_id("t", *validate("Ecosystems", t)) for t in (composed, decomposed, with_control)}
    assert len(ids) == 1


def test_same_content_in_two_tenants_gets_distinct_identities():
    a, b = lab_tenant(), lab_tenant()
    ra = publish_documents(a, [document()])["resource_ids"][0]
    rb = publish_documents(b, [document()])["resource_ids"][0]
    assert ra != rb
    assert resource_count(a) == 1 and resource_count(b) == 1


def test_validation_rejects_out_of_range_input_before_anything_is_written():
    with pytest.raises(ValueError):
        validate("ab", "x" * 40)
    with pytest.raises(ValueError):
        validate("Valid title", "too short")
    tenant = lab_tenant()
    with pytest.raises(SourceError) as caught:
        publish_documents(tenant, [document(text="short")])
    assert caught.value.status == 422
    assert resource_count(tenant) == 0


def test_index_failure_publishes_nothing(monkeypatch):
    tenant = lab_tenant()
    monkeypatch.setattr(pipeline, "vector_index", lambda: FailingIndex(RuntimeError("vector store unavailable")))
    monkeypatch.setattr(pipeline, "graph_index", lambda: NoopGraph())
    with pytest.raises(RuntimeError):
        publish_documents(tenant, [document()])
    assert resource_count(tenant) == 0
    assert generation(tenant) in (None, 0)


def test_graph_failure_after_vector_write_leaves_no_visible_resource(monkeypatch):
    tenant = lab_tenant()
    publish_documents(tenant, [document("Wetland plants", "Reeds and sedges stabilize wetland soil and store carbon.")])
    gen = generation(tenant)

    class GraphDown:
        name = "graph-down"

        def upsert_support(self, *args):
            raise RuntimeError("graph store unavailable")

    class VectorAccepts(NoopGraph):
        name = "sqlite"

    monkeypatch.setattr(pipeline, "vector_index", lambda: VectorAccepts())
    monkeypatch.setattr(pipeline, "graph_index", lambda: GraphDown())
    with pytest.raises(RuntimeError):
        publish_documents(tenant, [document("Tidal ponds", "Tide pools host crabs and anemones that tolerate drying.")])
    assert generation(tenant) == gen
    assert resource_count(tenant) == 1
    assert all(r["title"] != "Tidal ponds" for r in retrieve(tenant, "tide pools crabs anemones"))


def test_orphan_index_hits_for_unpublished_resources_are_never_returned(monkeypatch):
    tenant = lab_tenant()
    publish_documents(tenant, [document("Dunes", "Coastal dunes trap windblown sand and host grasses.")])

    class LeakyIndex:
        name = "leaky"

        def rank(self, tenant_id, generation_, question, candidates):
            return [("res-ghost-unpublished", 0.99)] + [(d["id"], 0.1) for d in candidates]

    monkeypatch.setattr("app.retrieval.vector_index", lambda: LeakyIndex())
    results = retrieve(tenant, "coastal dunes sand grasses")
    assert [r["title"] for r in results] == ["Dunes"]


def test_conflicting_publication_retries_with_a_fresh_generation(monkeypatch):
    tenant = lab_tenant()
    publish_documents(tenant, [document("Estuaries", "Estuaries mix fresh and salt water, supporting nursery habitat.")])
    raced = {"done": False}

    def race_once(real, tenant_id, expected, target, prepared, topic_label=None):
        if not raced["done"]:
            raced["done"] = True
            sql("UPDATE index_manifests SET generation=generation+1 WHERE tenant_id=?", (tenant_id,))
        return real.publish_commit(tenant_id, expected, target, prepared, topic_label)

    monkeypatch.setattr(pipeline, "source", lambda: ProxySource(source(), race_once))
    monkeypatch.setattr(pipeline, "vector_index", lambda: type("V", (), {"name": "sqlite", "index_chunks": lambda *a: None})())
    monkeypatch.setattr(pipeline, "graph_index", lambda: NoopGraph())
    result = publish_documents(tenant, [document("Salt marsh", "Salt marsh grasses tolerate flooding and saline soil.")])
    assert result["changed"] is True
    assert generation(tenant) == 3  # base 1, concurrent publication 2, ours 3
    assert resource_count(tenant) == 2


def test_gives_up_with_no_partial_publication_after_repeated_conflicts(monkeypatch):
    tenant = lab_tenant()
    publish_documents(tenant, [document("Lagoons", "Lagoons are shallow bodies of water separated from the sea.")])
    gen = generation(tenant)

    def always_race(real, tenant_id, expected, target, prepared, topic_label=None):
        sql("UPDATE index_manifests SET generation=generation+1 WHERE tenant_id=?", (tenant_id,))
        return real.publish_commit(tenant_id, expected, target, prepared, topic_label)

    monkeypatch.setattr(pipeline, "source", lambda: ProxySource(source(), always_race))
    monkeypatch.setattr(pipeline, "vector_index", lambda: type("V", (), {"name": "sqlite", "index_chunks": lambda *a: None})())
    monkeypatch.setattr(pipeline, "graph_index", lambda: NoopGraph())
    with pytest.raises(pipeline.PublicationConflict):
        publish_documents(tenant, [document("Reefs", "Coral reefs build limestone structures in warm shallow seas.")])
    assert resource_count(tenant) == 1
    assert generation(tenant) == gen + pipeline.MAX_PUBLISH_ATTEMPTS


def test_web_cache_for_the_same_url_never_crosses_tenants():
    a, b = lab_tenant(), lab_tenant()
    url = "https://en.wikipedia.org/wiki/Lichen"
    publish_documents(a, [{"title": "Lichen", "text": "Lichens are symbiotic partnerships of fungi and algae.", "kind": "Web result", "source_uri": url, "source_version": "2026-01-01"}])
    publish_documents(b, [{"title": "Lichen", "text": "Tenant B cached a different excerpt for the same article.", "kind": "Web result", "source_uri": url, "source_version": "2026-01-01"}])
    rows = {r["tenant_id"]: r["text"] for r in query_all("SELECT tenant_id, text FROM resources WHERE source_uri=?", (url,))}
    assert rows[a].startswith("Lichens are")
    assert rows[b].startswith("Tenant B")
    hits = _search_local(a, "symbiotic fungi algae lichen", 5, exclude_web=False)
    assert hits and hits[0]["text"].startswith("Lichens are")
    assert all(not h["text"].startswith("Tenant B") for h in hits)


def test_web_cache_purge_removes_only_expired_results_and_advances_generation():
    tenant = lab_tenant()
    old_time = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    old = {"title": "Old cache entry", "text": "An expired excerpt cached long ago for a past question.", "kind": "Web result",
           "source_uri": "https://en.wikipedia.org/wiki/Old_entry", "source_version": "2025-01-01", "created_at": old_time}
    fresh = {"title": "Fresh cache entry", "text": "A recent excerpt cached for a current question about tides.", "kind": "Web result",
             "source_uri": "https://en.wikipedia.org/wiki/Fresh_entry", "source_version": "2026-11-01"}
    publish_documents(tenant, [old, fresh])
    gen = generation(tenant)
    result = purge_web(tenant, days=30)
    assert result["deleted"] == 1
    assert generation(tenant) == gen + 1
    assert [r["title"] for r in query_all("SELECT title FROM resources WHERE tenant_id=?", (tenant,))] == ["Fresh cache entry"]
    assert purge_web(tenant, days=30)["deleted"] == 0  # idempotent


def test_purge_removes_index_entries_for_deleted_resources(monkeypatch):
    tenant = lab_tenant()
    old_time = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    publish_documents(tenant, [{"title": "Expiring", "text": "This cached page will be purged from every derived index.",
                                "kind": "Web result", "source_uri": "https://en.wikipedia.org/wiki/Expiring", "created_at": old_time}])
    deleted = {}

    class RecordingVector:
        name = "recording"

        def delete_resources(self, tenant_id, ids):
            deleted["vector"] = list(ids)

    class RecordingGraph:
        name = "recording"

        def delete_resources(self, tenant_id, ids):
            deleted["graph"] = list(ids)

    monkeypatch.setattr(pipeline, "vector_index", lambda: RecordingVector())
    monkeypatch.setattr(pipeline, "graph_index", lambda: RecordingGraph())
    purge_web(tenant, days=30)
    assert len(deleted["vector"]) == 1 and deleted["vector"] == deleted["graph"]


# ------------------------------------------------------------------ chunking
def test_chunks_respect_the_limit_and_overlap_by_one_sentence():
    text = " ".join(f"Sentence number {i} describes one measurable fact about tides." for i in range(40))
    chunks = chunk_text(text, max_chars=200, overlap=1)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    for previous, following in zip(chunks, chunks[1:]):
        last_sentence = previous.split(". ")[-1].rstrip(".")
        assert last_sentence in following


def test_a_single_overlong_sentence_is_split_without_losing_words():
    text = "word " * 300
    chunks = chunk_text(text.strip(), max_chars=100, overlap=0)
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_chunk_identifiers_are_stable_across_replays():
    tenant = lab_tenant()
    publish_documents(tenant, [document("Chunked", "First sentence about wetlands. Second sentence about estuaries. " * 12)])
    first = pipeline.build_chunks([{"id": "res-x", "title": "Chunked", "text": "A. B. " * 200}])
    second = pipeline.build_chunks([{"id": "res-x", "title": "Chunked", "text": "A. B. " * 200}])
    assert [c["id"] for c in first] == [c["id"] for c in second]
    assert len({c["id"] for c in first}) == len(first)
