"""Staged ingestion with idempotent, atomic publication.

Stages, in order:
  1. validate        reject out-of-range input after Unicode normalization
  2. normalize       NFC, strip control characters, collapse whitespace
  3. identify        content-addressed resource ID (duplicate content converges to one row)
  4. extract         rule-based skill entities (explicit skill_id plus skill-name matches)
  5. index           write vector and graph points tagged with the target generation
  6. publish         one SQLite transaction: compare-and-swap the generation, then insert rows

Visibility gate: derived index points are only ever read after SQLite publication, and reads
intersect index hits with published rows. A failure before step 6 leaves orphan index points
that no query can return.
"""
import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone

from .db import db
from .indexes import SQLiteGraphIndex, graph_index, vector_index

log = logging.getLogger("kag.ingestion")

MAX_PUBLISH_ATTEMPTS = 3
TITLE_LIMITS = (3, 180)
TEXT_LIMITS = (30, 50000)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PublicationConflict(RuntimeError):
    """Another publication advanced the generation between staging and commit."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate(title: str, text: str) -> tuple[str, str]:
    title = _CONTROL.sub("", unicodedata.normalize("NFC", title or "")).strip()
    text = _CONTROL.sub("", unicodedata.normalize("NFC", text or ""))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not TITLE_LIMITS[0] <= len(title) <= TITLE_LIMITS[1]:
        raise ValueError("Title length is outside the allowed range")
    if not TEXT_LIMITS[0] <= len(text) <= TEXT_LIMITS[1]:
        raise ValueError("Text length is outside the allowed range")
    return title, text


def content_resource_id(tenant_id: str, title: str, text: str) -> str:
    # Tenant is part of the hash so identical content in two tenants never shares a row.
    digest = hashlib.sha256(f"{title.lower()}\n{text}".encode()).hexdigest()
    return "res-" + hashlib.sha256(f"{tenant_id}|{digest}".encode()).hexdigest()[:24]


def extract_entities(connection, tenant_id: str, text: str, skill_id: str | None) -> list[str]:
    lowered = text.lower()
    skills = connection.execute("SELECT id, name FROM skills WHERE tenant_id=?", (tenant_id,)).fetchall()
    found = {s["id"] for s in skills if s["name"].lower() in lowered}
    if skill_id:
        found.add(skill_id)
    return sorted(found)


def _prepare(connection, tenant_id: str, documents: list[dict]) -> list[dict]:
    prepared = []
    for doc in documents:
        title, text = validate(doc["title"], doc["text"])
        resource_id = content_resource_id(tenant_id, title, text)
        exists = connection.execute("SELECT 1 FROM resources WHERE id=? AND tenant_id=?", (resource_id, tenant_id)).fetchone()
        skill_id = doc.get("skill_id")
        prepared.append({
            "id": resource_id,
            "title": title,
            "text": text,
            "skill_id": skill_id,
            "kind": doc.get("kind", "Imported text"),
            "source_uri": doc.get("source_uri") or f"local://imports/{resource_id}",
            "source_version": doc.get("source_version", "v1"),
            "created_at": doc.get("created_at") or now(),
            "entities": extract_entities(connection, tenant_id, text, skill_id),
            "new": exists is None,
        })
    return prepared


def _ensure_manifest(connection, tenant_id: str) -> int:
    connection.execute("INSERT OR IGNORE INTO index_manifests VALUES (?,?,?)", (tenant_id, 0, now()))
    return connection.execute("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant_id,)).fetchone()["generation"]


def publish(tenant_id: str, documents: list[dict], topic_label: str | None = None) -> dict:
    """Index and publish documents for one tenant. Returns resource IDs and whether anything changed.

    Retries on publication conflicts with a fresh generation. Replaying identical content is a
    no-op that returns the existing resource IDs.
    """
    for _ in range(MAX_PUBLISH_ATTEMPTS):
        with db() as connection:
            current = _ensure_manifest(connection, tenant_id)
            prepared = _prepare(connection, tenant_id, documents)
        pending = [d for d in prepared if d["new"]]
        if not pending:
            return {"resource_ids": [d["id"] for d in prepared], "generation": current, "changed": False}

        target = current + 1
        # Steps 5: derived index writes at the target generation. Invisible until step 6 commits.
        vector_index().upsert_resources(tenant_id, target, pending)
        graph_index().upsert_support(tenant_id, target, [(d["id"], e) for d in pending for e in d["entities"]])

        try:
            with db() as connection:
                # Step 6: compare-and-swap. A concurrent publication makes this affect zero rows.
                swapped = connection.execute(
                    "UPDATE index_manifests SET generation=?, updated_at=? WHERE tenant_id=? AND generation=?",
                    (target, now(), tenant_id, current),
                )
                if swapped.rowcount != 1:
                    raise PublicationConflict("Generation advanced during publication")
                for d in prepared:
                    connection.execute(
                        "INSERT OR IGNORE INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (d["id"], tenant_id, d["title"], d["text"], d["skill_id"], d["kind"], d["source_uri"], d["source_version"], "curriculum", d["created_at"]),
                    )
                    for skill in d["entities"]:
                        connection.execute("INSERT OR IGNORE INTO resource_entities VALUES (?,?,?)", (tenant_id, d["id"], skill))
                if topic_label:
                    topic_id = "topic-" + hashlib.sha256(f"{tenant_id}|{topic_label.lower()}".encode()).hexdigest()[:16]
                    connection.execute("INSERT OR IGNORE INTO topics VALUES (?,?,?,?)", (topic_id, tenant_id, topic_label, now()))
                    connection.executemany(
                        "INSERT OR IGNORE INTO topic_resources VALUES (?,?)",
                        [(topic_id, d["id"]) for d in prepared],
                    )
            return {"resource_ids": [d["id"] for d in prepared], "generation": target, "changed": True}
        except PublicationConflict:
            log.warning("Publication conflict for tenant; retrying with a fresh generation")
    raise PublicationConflict("Publication failed after retries; no partial generation was published")


def rebuild_indexes() -> None:
    """Rebuild derived indexes from published SQLite rows. Safe to run repeatedly.

    SQLite is the source of truth; this restores Qdrant or Neo4j after a wipe or a fresh
    deployment. Skipped entirely when the offline backends are selected.
    """
    vector, graph = vector_index(), graph_index()
    if vector.name == "sqlite" and graph.name == "sqlite":
        return
    with db() as connection:
        manifests = [dict(r) for r in connection.execute("SELECT tenant_id, generation FROM index_manifests")]
        for manifest in manifests:
            tenant, generation = manifest["tenant_id"], manifest["generation"]
            documents = [dict(r) for r in connection.execute(
                "SELECT id, title, text FROM resources WHERE tenant_id=? AND auth_scope='curriculum'", (tenant,))]
            pairs = SQLiteGraphIndex().support_pairs(tenant, generation)
            vector.upsert_resources(tenant, generation, documents)
            graph.upsert_support(tenant, generation, sorted(pairs))
    log.info("Rebuilt derived indexes for %d tenant(s)", len(manifests))
