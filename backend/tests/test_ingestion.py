"""Ingestion: idempotent convergence, all-or-nothing publication, and visibility gating."""
import os
import tempfile
import unicodedata
from uuid import uuid4

os.environ.setdefault("KAG_DATA_DIR", tempfile.mkdtemp(prefix="kag-ingestion-test-"))
os.environ["KAG_LOAD_DOTENV"] = "false"
os.environ["WEB_SEARCH_ENABLED"] = "false"  # no network in tests

import pytest

from app import ingestion
from app.db import db, initialize
from app.ingestion import PublicationConflict, content_resource_id, publish, validate
from app.retrieval import retrieve


@pytest.fixture(autouse=True)
def schema():
    initialize()


def lab_tenant() -> str:
    return f"lab-ingest-{uuid4().hex[:8]}"


def document(title="Coastal ecosystems", text=None):
    return {"title": title, "text": text or "Tidal marshes filter sediment and shelter juvenile fish. Synthetic lab text."}


def generation(tenant):
    with db() as c:
        row = c.execute("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant,)).fetchone()
    return row["generation"] if row else None


def resource_count(tenant):
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM resources WHERE tenant_id=?", (tenant,)).fetchone()[0]


class FailingIndex:
    name = "failing"

    def __init__(self, error):
        self.error = error

    def upsert_resources(self, *args):
        raise self.error

    def upsert_support(self, *args):
        raise self.error

    def rank(self, *args):
        return []


def test_identical_content_converges_to_one_resource_and_no_generation_bump():
    tenant = lab_tenant()
    first = publish(tenant, [document()])
    gen = generation(tenant)
    second = publish(tenant, [document(text="  " + document()["text"] + "\n\n\n")])
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["resource_ids"] == first["resource_ids"]
    assert generation(tenant) == gen
    assert resource_count(tenant) == 1


def test_unicode_and_control_characters_normalize_to_the_same_identity():
    composed = "Café ecosystems. " + "Tidal marshes filter sediment and shelter fish in estuaries."
    decomposed = unicodedata.normalize("NFD", composed)
    with_control = composed.replace("Café", "Caf\x07é")
    assert content_resource_id("t", *validate("Ecosystems", composed)) == content_resource_id("t", *validate("Ecosystems", decomposed))
    assert content_resource_id("t", *validate("Ecosystems", composed)) == content_resource_id("t", *validate("Ecosystems", with_control))


def test_same_content_in_two_tenants_gets_distinct_identities():
    a, b = lab_tenant(), lab_tenant()
    ra = publish(a, [document()])["resource_ids"][0]
    rb = publish(b, [document()])["resource_ids"][0]
    assert ra != rb
    assert resource_count(a) == 1 and resource_count(b) == 1


def test_validation_rejects_out_of_range_input():
    with pytest.raises(ValueError):
        validate("ab", "x" * 40)  # title too short
    with pytest.raises(ValueError):
        validate("Valid title", "too short")  # text too short


def test_index_failure_publishes_nothing(monkeypatch):
    tenant = lab_tenant()
    monkeypatch.setattr(ingestion, "vector_index", lambda: FailingIndex(RuntimeError("vector store unavailable")))
    with pytest.raises(RuntimeError):
        publish(tenant, [document()])
    assert resource_count(tenant) == 0
    assert generation(tenant) == 0


def test_graph_failure_after_vector_write_leaves_no_visible_resource(monkeypatch):
    tenant = lab_tenant()
    publish(tenant, [document("Wetland plants", "Reeds and sedges stabilize wetland soil and store carbon.")])
    gen = generation(tenant)

    class GraphDown:
        name = "graph-down"

        def upsert_support(self, *args):
            raise RuntimeError("graph store unavailable")

    class RealVectorWriter:
        name = "sqlite"

        def upsert_resources(self, *args):
            return None

        def rank(self, *args):
            return []

    monkeypatch.setattr(ingestion, "vector_index", lambda: RealVectorWriter())
    monkeypatch.setattr(ingestion, "graph_index", lambda: GraphDown())
    with pytest.raises(RuntimeError):
        publish(tenant, [document("Tidal ponds", "Tide pools host crabs and anemones that tolerate drying.")])
    assert generation(tenant) == gen
    assert resource_count(tenant) == 1
    results = retrieve(tenant, "tide pools crabs anemones")
    assert all(r["title"] != "Tidal ponds" for r in results)


def test_orphan_index_hits_for_unpublished_resources_are_never_returned(monkeypatch):
    tenant = lab_tenant()
    publish(tenant, [document("Dunes", "Coastal dunes trap windblown sand and host grasses.")])

    class LeakyIndex:
        name = "leaky"

        def rank(self, tenant_id, generation_, question, candidates):
            # Simulates a Qdrant point written by a failed publication.
            return [("res-ghost-unpublished", 0.99)] + [(d["id"], 0.1) for d in candidates]

        def upsert_resources(self, *args):
            return None

    monkeypatch.setattr("app.retrieval.vector_index", lambda: LeakyIndex())
    results = retrieve(tenant, "coastal dunes sand grasses")
    assert [r["title"] for r in results] == ["Dunes"]
    assert all(r["id"] != "res-ghost-unpublished" for r in results)


def test_conflicting_publication_retries_with_a_fresh_generation(monkeypatch):
    tenant = lab_tenant()
    publish(tenant, [document("Estuaries", "Estuaries mix fresh and salt water, supporting nursery habitat.")])

    class RacingIndex:
        """Performs a concurrent publication once, between staging and commit."""
        name = "racing"

        def __init__(self):
            self.raced = False

        def upsert_resources(self, tenant_id, gen, docs):
            if not self.raced:
                self.raced = True
                with db() as c:
                    c.execute("UPDATE index_manifests SET generation=generation+1 WHERE tenant_id=?", (tenant_id,))

        def rank(self, *args):
            return []

    racer = RacingIndex()  # one instance: the race happens once, on the first attempt only
    monkeypatch.setattr(ingestion, "vector_index", lambda: racer)
    monkeypatch.setattr(ingestion, "graph_index", lambda: type("G", (), {"upsert_support": lambda *a: None})())
    result = publish(tenant, [document("Salt marsh", "Salt marsh grasses tolerate flooding and saline soil.")])
    assert result["changed"] is True
    assert generation(tenant) == 3  # base 1, concurrent publish 2, our publish 3
    assert resource_count(tenant) == 2


def test_gives_up_with_no_partial_publication_after_repeated_conflicts(monkeypatch):
    tenant = lab_tenant()
    publish(tenant, [document("Lagoons", "Lagoons are shallow bodies of water separated from the sea.")])
    gen = generation(tenant)

    class AlwaysRacing:
        name = "always-racing"

        def upsert_resources(self, tenant_id, g, docs):
            with db() as c:
                c.execute("UPDATE index_manifests SET generation=generation+1 WHERE tenant_id=?", (tenant_id,))

        def rank(self, *args):
            return []

    monkeypatch.setattr(ingestion, "vector_index", lambda: AlwaysRacing())
    monkeypatch.setattr(ingestion, "graph_index", lambda: type("G", (), {"upsert_support": lambda *a: None})())
    with pytest.raises(PublicationConflict):
        publish(tenant, [document("Reefs", "Coral reefs build limestone structures in warm shallow seas.")])
    assert resource_count(tenant) == 1
    assert generation(tenant) == gen + ingestion.MAX_PUBLISH_ATTEMPTS  # manifest advanced by the racer only


def test_web_cache_for_the_same_url_never_crosses_tenants():
    a, b = lab_tenant(), lab_tenant()
    url = "https://en.wikipedia.org/wiki/Lichen"
    publish(a, [{"title": "Lichen", "text": "Lichens are symbiotic partnerships of fungi and algae.", "kind": "Web result", "source_uri": url, "source_version": "2026-01-01"}])
    publish(b, [{"title": "Lichen", "text": "Tenant B cached a different excerpt for the same article.", "kind": "Web result", "source_uri": url, "source_version": "2026-01-01"}])
    with db() as c:
        rows = {r["tenant_id"]: r["text"] for r in c.execute("SELECT tenant_id, text FROM resources WHERE source_uri=?", (url,))}
    assert rows[a].startswith("Lichens are")
    assert rows[b].startswith("Tenant B")
    # Cached web rows are excluded from curriculum search by design, so check the tenant's own
    # row through the internal search that includes them.
    from app.retrieval import _search_local
    hits = _search_local(a, "symbiotic fungi algae lichen", 5, exclude_web=False)
    assert hits and hits[0]["text"].startswith("Lichens are")
    assert all(h["text"].startswith("Tenant B") is False for h in hits)
