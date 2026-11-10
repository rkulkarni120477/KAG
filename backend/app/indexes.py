"""Derived index adapters behind stable interfaces.

SQLite remains the source of truth. Vector and graph indexes are derived from published
SQLite rows and are selected by environment:

- KAG_VECTOR_BACKEND = sqlite (default, offline) | qdrant
- KAG_GRAPH_BACKEND  = sqlite (default, offline) | neo4j

Every index write carries a generation number. Index hits are only used when the same
resource is also published in SQLite, so an unpublished (failed or in-flight) ingestion can
never surface, even if its points already exist in Qdrant or Neo4j.
"""
import os
import threading
import uuid

from .db import db
from .text import DIMENSION, embedding

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "kag_resources")
_lock = threading.Lock()
_instances: dict = {}


# ---------------------------------------------------------------- vector ports
class SQLiteVectorIndex:
    """Offline hybrid ranking: the hashed vector is recomputed from published rows."""

    name = "sqlite"

    def upsert_resources(self, tenant_id, generation, documents):
        # Nothing to write: published rows in SQLite are the index for this backend.
        return None

    def rank(self, tenant_id, generation, question, candidates):
        query = embedding(question)
        scored = [(d["id"], sum(a * b for a, b in zip(query, embedding(d["title"] + " " + d["text"])))) for d in candidates]
        return sorted(scored, key=lambda x: x[1], reverse=True)


class QdrantVectorIndex:
    name = "qdrant"

    def __init__(self):
        from qdrant_client import QdrantClient
        from qdrant_client.http import models

        self.models = models
        self.client = QdrantClient(
            url=os.environ["QDRANT_URL"],
            api_key=os.getenv("QDRANT_API_KEY") or None,
            timeout=10,
        )
        if not self.client.collection_exists(QDRANT_COLLECTION):
            self.client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=models.VectorParams(size=DIMENSION, distance=models.Distance.COSINE),
            )
            self.client.create_payload_index(QDRANT_COLLECTION, "tenant_id", models.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(QDRANT_COLLECTION, "generation", models.PayloadSchemaType.INTEGER)

    @staticmethod
    def point_id(resource_id: str) -> str:
        # Qdrant point IDs must be UUIDs or integers; the resource ID is kept in the payload.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, resource_id))

    def upsert_resources(self, tenant_id, generation, documents):
        if not documents:
            return
        points = [
            self.models.PointStruct(
                id=self.point_id(d["id"]),
                vector=embedding(d["title"] + " " + d["text"]),
                payload={"tenant_id": tenant_id, "resource_id": d["id"], "generation": generation},
            )
            for d in documents
        ]
        # Upsert is idempotent: replaying the same resource overwrites its point.
        self.client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)

    def rank(self, tenant_id, generation, question, candidates):
        candidate_ids = {d["id"] for d in candidates}
        if not candidate_ids:
            return []
        models = self.models
        response = self.client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=embedding(question),
            query_filter=models.Filter(must=[
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                models.FieldCondition(key="generation", range=models.Range(lte=generation)),
            ]),
            # Tenant-filtered top-k large enough to cover every published candidate.
            limit=max(100, len(candidate_ids) * 10),
            with_payload=True,
        )
        ranked = []
        for point in response.points:
            resource_id = point.payload.get("resource_id")
            if resource_id in candidate_ids:
                ranked.append((resource_id, point.score))
        return ranked


# ---------------------------------------------------------------- graph ports
class SQLiteGraphIndex:
    name = "sqlite"

    def upsert_support(self, tenant_id, generation, pairs):
        return None

    def support_pairs(self, tenant_id, generation):
        with db() as connection:
            rows = connection.execute(
                "SELECT id AS resource_id, skill_id FROM resources WHERE tenant_id=? AND skill_id IS NOT NULL "
                "UNION SELECT resource_id, skill_id FROM resource_entities WHERE tenant_id=?",
                (tenant_id, tenant_id),
            ).fetchall()
        return {(r["resource_id"], r["skill_id"]) for r in rows}


class Neo4jGraphIndex:
    name = "neo4j"

    def __init__(self):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
        )
        self.driver.verify_connectivity()

    def upsert_support(self, tenant_id, generation, pairs):
        if not pairs:
            return
        rows = [{"resource": r, "skill": s} for r, s in pairs]
        # MERGE keeps replays idempotent. Every node and edge is tagged with the generation
        # that wrote it, and reads filter on that tag.
        self.driver.execute_query(
            """
            UNWIND $rows AS row
            MERGE (r:Resource {tenant_id:$tenant, id:row.resource})
            SET r.generation = CASE WHEN r.generation IS NULL OR r.generation < $gen THEN $gen ELSE r.generation END
            MERGE (s:Skill {tenant_id:$tenant, id:row.skill})
            MERGE (r)-[e:SUPPORTS]->(s)
            SET e.generation = CASE WHEN e.generation IS NULL OR e.generation < $gen THEN $gen ELSE e.generation END
            """,
            rows=rows, tenant=tenant_id, gen=generation,
            database_=os.getenv("NEO4J_DATABASE", "neo4j"),
        )

    def support_pairs(self, tenant_id, generation):
        records, _, _ = self.driver.execute_query(
            """
            MATCH (r:Resource {tenant_id:$tenant})-[e:SUPPORTS]->(s:Skill {tenant_id:$tenant})
            WHERE e.generation <= $gen
            RETURN r.id AS resource_id, s.id AS skill_id
            """,
            tenant=tenant_id, gen=generation,
            database_=os.getenv("NEO4J_DATABASE", "neo4j"),
        )
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
