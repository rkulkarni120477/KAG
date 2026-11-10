"""Derived index adapters behind stable interfaces.

The source service is the source of truth. Vector and graph indexes are derived from published rows:

- KAG_VECTOR_BACKEND = sqlite (default, offline, hashed embeddings) | qdrant (configured embedder)
- KAG_GRAPH_BACKEND  = sqlite (default, offline) | neo4j

Index writes carry the generation they were produced for. Reads intersect index hits with resources
the source service has published, so an unpublished (failed or in-flight) ingestion never surfaces.
"""
import os
import threading
import uuid

from .domain.documents import chunk_text
from .embeddings import HashedEmbedder, embedder
from .source_client import source

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "kag_chunks")
_lock = threading.Lock()
_instances: dict = {}
_offline_embedder = HashedEmbedder()


def _max_per_resource(scored, candidate_ids):
    best: dict[str, float] = {}
    for resource_id, score in scored:
        if resource_id in candidate_ids and score > best.get(resource_id, float("-inf")):
            best[resource_id] = score
    return sorted(best.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------- vector ports
class SQLiteVectorIndex:
    """Offline hybrid ranking. Vectors are recomputed from published text with the hashed embedder."""

    name = "sqlite"

    def index_chunks(self, tenant_id, generation, chunks):
        return None  # published rows are the index for this backend

    def delete_resources(self, tenant_id, resource_ids):
        return None

    def rank(self, tenant_id, generation, question, candidates):
        query = _offline_embedder.embed_query(question)
        scored = []
        for d in candidates:
            pieces = chunk_text(d["title"] + ". " + d["text"]) or [d["text"]]
            for vector in _offline_embedder.embed_documents(pieces):
                scored.append((d["id"], sum(a * b for a, b in zip(query, vector))))
        return _max_per_resource(scored, {d["id"] for d in candidates})


class QdrantVectorIndex:
    name = "qdrant"

    def __init__(self):
        from qdrant_client import QdrantClient
        from qdrant_client.http import models

        self.models = models
        self.embedder = embedder()
        self.collection = f"{QDRANT_COLLECTION}_{self.embedder.name}_{self.embedder.dimension}"
        self.client = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.getenv("QDRANT_API_KEY") or None, timeout=10)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(size=self.embedder.dimension, distance=models.Distance.COSINE),
            )
            self.client.create_payload_index(self.collection, "tenant_id", models.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(self.collection, "generation", models.PayloadSchemaType.INTEGER)
            self.client.create_payload_index(self.collection, "resource_id", models.PayloadSchemaType.KEYWORD)

    @staticmethod
    def point_id(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    def index_chunks(self, tenant_id, generation, chunks):
        """chunks: [{id, resource_id, ordinal, vector}]. Upserts are idempotent per chunk ID."""
        if not chunks:
            return
        points = [self.models.PointStruct(
            id=self.point_id(c["id"]), vector=c["vector"],
            payload={"tenant_id": tenant_id, "resource_id": c["resource_id"], "ordinal": c["ordinal"], "generation": generation},
        ) for c in chunks]
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def delete_resources(self, tenant_id, resource_ids):
        if not resource_ids:
            return
        m = self.models
        self.client.delete(collection_name=self.collection, points_selector=m.Filter(must=[
            m.FieldCondition(key="tenant_id", match=m.MatchValue(value=tenant_id)),
            m.FieldCondition(key="resource_id", match=m.MatchAny(any=list(resource_ids))),
        ]), wait=True)

    def rank(self, tenant_id, generation, question, candidates):
        candidate_ids = {d["id"] for d in candidates}
        if not candidate_ids:
            return []
        m = self.models
        response = self.client.query_points(
            collection_name=self.collection,
            query=self.embedder.embed_query(question),
            query_filter=m.Filter(must=[
                m.FieldCondition(key="tenant_id", match=m.MatchValue(value=tenant_id)),
                m.FieldCondition(key="generation", range=m.Range(lte=generation)),
            ]),
            # Tenant-filtered top-k large enough to cover chunks for every candidate resource.
            limit=max(200, len(candidate_ids) * 20),
            with_payload=True,
        )
        return _max_per_resource(((p.payload["resource_id"], p.score) for p in response.points), candidate_ids)


# ---------------------------------------------------------------- graph ports
class SQLiteGraphIndex:
    name = "sqlite"

    def upsert_support(self, tenant_id, generation, pairs):
        return None

    def delete_resources(self, tenant_id, resource_ids):
        return None

    def support_pairs(self, tenant_id, generation):
        return {tuple(p) for p in source().snapshot(tenant_id, include_web=True)["support"]}


class Neo4jGraphIndex:
    name = "neo4j"

    def __init__(self):
        from neo4j import GraphDatabase

        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))
        self.driver.verify_connectivity()

    def upsert_support(self, tenant_id, generation, pairs):
        if not pairs:
            return
        self.driver.execute_query(
            """
            UNWIND $rows AS row
            MERGE (r:Resource {tenant_id:$tenant, id:row.resource})
            SET r.generation = CASE WHEN r.generation IS NULL OR r.generation < $gen THEN $gen ELSE r.generation END
            MERGE (s:Skill {tenant_id:$tenant, id:row.skill})
            MERGE (r)-[e:SUPPORTS]->(s)
            SET e.generation = CASE WHEN e.generation IS NULL OR e.generation < $gen THEN $gen ELSE e.generation END
            """,
            rows=[{"resource": r, "skill": s} for r, s in pairs], tenant=tenant_id, gen=generation, database_=self.database)

    def delete_resources(self, tenant_id, resource_ids):
        if not resource_ids:
            return
        self.driver.execute_query("MATCH (r:Resource {tenant_id:$tenant}) WHERE r.id IN $ids DETACH DELETE r",
                                  tenant=tenant_id, ids=list(resource_ids), database_=self.database)

    def support_pairs(self, tenant_id, generation):
        records, _, _ = self.driver.execute_query(
            "MATCH (r:Resource {tenant_id:$tenant})-[e:SUPPORTS]->(s:Skill {tenant_id:$tenant}) WHERE e.generation <= $gen "
            "RETURN r.id AS resource_id, s.id AS skill_id",
            tenant=tenant_id, gen=generation, database_=self.database)
        return {(r["resource_id"], r["skill_id"]) for r in records}


# ---------------------------------------------------------------- factories
def _singleton(key, factory):
    with _lock:
        if key not in _instances:
            _instances[key] = factory()
        return _instances[key]


def vector_index():
    backend = os.getenv("KAG_VECTOR_BACKEND", "sqlite").lower()
    if backend == "qdrant":
        return _singleton("vector", QdrantVectorIndex)
    if backend != "sqlite":
        raise RuntimeError(f"Unknown KAG_VECTOR_BACKEND: {backend}")
    return _singleton("vector", SQLiteVectorIndex)


def graph_index():
    backend = os.getenv("KAG_GRAPH_BACKEND", "sqlite").lower()
    if backend == "neo4j":
        return _singleton("graph", Neo4jGraphIndex)
    if backend != "sqlite":
        raise RuntimeError(f"Unknown KAG_GRAPH_BACKEND: {backend}")
    return _singleton("graph", SQLiteGraphIndex)


def reset_instances() -> None:
    """Drop cached adapters (tests switch backends)."""
    with _lock:
        _instances.clear()
