"""All SQL for the source service. Every function is tenant-scoped; cross-tenant rows are excluded in SQL."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..domain.documents import content_resource_id, extract_entities, validate
from .db import db

MAX_PUBLISH_ATTEMPTS = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(connection, sql, params=()):
    return [dict(r) for r in connection.execute(sql, params).fetchall()]


# ------------------------------------------------------------------ identity and scope
def user_identity(tenant_id: str, user_id: str):
    with db() as c:
        row = c.execute("SELECT id, tenant_id, name, email, role FROM users WHERE id=? AND tenant_id=?", (user_id, tenant_id)).fetchone()
    return dict(row) if row else None


def roster(tenant_id: str, user_id: str) -> list[dict]:
    with db() as c:
        return _rows(c, "SELECT s.id, s.name, s.grade FROM students s JOIN access_grants g ON g.student_id=s.id "
                        "WHERE s.tenant_id=? AND g.user_id=? ORDER BY s.name", (tenant_id, user_id))


def student(tenant_id: str, student_id: str):
    with db() as c:
        row = c.execute("SELECT * FROM students WHERE id=? AND tenant_id=?", (student_id, tenant_id)).fetchone()
    return dict(row) if row else None


def is_granted(tenant_id: str, user_id: str, student_id: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM access_grants g JOIN students s ON s.id=g.student_id "
                         "WHERE g.user_id=? AND g.student_id=? AND s.tenant_id=?", (user_id, student_id, tenant_id)).fetchone() is not None


def progress(tenant_id: str, student_id: str) -> list[dict]:
    with db() as c:
        return _rows(c, "SELECT p.skill_id, p.score, p.assessed_at, p.assessment_id, a.name AS assessment_name "
                        "FROM student_progress p JOIN students s ON s.id=p.student_id "
                        "LEFT JOIN assessments a ON a.id=p.assessment_id "
                        "WHERE p.student_id=? AND s.tenant_id=?", (student_id, tenant_id))


def waivers(tenant_id: str, student_id: str) -> list[dict]:
    with db() as c:
        return _rows(c, "SELECT * FROM waivers WHERE tenant_id=? AND student_id=? ORDER BY created_at", (tenant_id, student_id))


def add_waiver(tenant_id: str, student_id: str, skill_id: str, reason: str, granted_by: str,
               effective_from: str, effective_until: str | None) -> dict:
    waiver = {"id": str(uuid4()), "tenant_id": tenant_id, "student_id": student_id, "skill_id": skill_id,
              "reason": reason, "granted_by": granted_by, "effective_from": effective_from,
              "effective_until": effective_until, "revoked_at": None, "created_at": now()}
    with db() as c:
        c.execute("INSERT INTO waivers VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(waiver.values()))
    return waiver


def revoke_waiver(tenant_id: str, waiver_id: str) -> bool:
    with db() as c:
        cur = c.execute("UPDATE waivers SET revoked_at=? WHERE id=? AND tenant_id=? AND revoked_at IS NULL", (now(), waiver_id, tenant_id))
        return cur.rowcount == 1


# ------------------------------------------------------------------ curriculum snapshot
def snapshot(tenant_id: str, include_web: bool) -> dict:
    with db() as c:
        manifest = c.execute("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant_id,)).fetchone()
        skills = _rows(c, "SELECT * FROM skills WHERE tenant_id=? ORDER BY position", (tenant_id,))
        rules = _rows(c, "SELECT r.* FROM prerequisite_rules r WHERE r.tenant_id=? "
                         "AND r.skill_id IN (SELECT id FROM skills WHERE tenant_id=?) "
                         "AND r.requires_id IN (SELECT id FROM skills WHERE tenant_id=?)", (tenant_id, tenant_id, tenant_id))
        policy = c.execute("SELECT threshold FROM academic_policies WHERE tenant_id=? AND threshold IS NOT NULL ORDER BY id LIMIT 1", (tenant_id,)).fetchone()
        standards = _rows(c, "SELECT * FROM curriculum_standards WHERE tenant_id=? ORDER BY id", (tenant_id,))
        courses = _rows(c, "SELECT * FROM courses WHERE tenant_id=?", (tenant_id,))
        web_filter = "" if include_web else " AND kind != 'Web result'"
        resources = _rows(c, "SELECT id, title, text, skill_id, kind, source_uri, source_version, created_at FROM resources "
                             "WHERE tenant_id=? AND auth_scope='curriculum'" + web_filter + " ORDER BY created_at, id", (tenant_id,))
        topics = _rows(c, "SELECT id, label, created_at FROM topics WHERE tenant_id=? ORDER BY created_at, id", (tenant_id,))
        topic_links = _rows(c, "SELECT tr.topic_id, tr.resource_id FROM topic_resources tr JOIN topics t ON t.id=tr.topic_id WHERE t.tenant_id=?", (tenant_id,))
        support = _rows(c, "SELECT resource_id, skill_id FROM resource_entities WHERE tenant_id=? "
                           "UNION SELECT id AS resource_id, skill_id FROM resources WHERE tenant_id=? AND skill_id IS NOT NULL", (tenant_id, tenant_id))
    return {
        "generation": manifest["generation"] if manifest else 0,
        "skills": skills, "rules": rules, "threshold": policy["threshold"] if policy else None,
        "standards": standards, "courses": courses, "resources": resources, "topics": topics,
        "topic_links": topic_links, "support": [[r["resource_id"], r["skill_id"]] for r in support],
    }


def evidence(tenant_id: str, resource_id: str):
    with db() as c:
        row = c.execute("SELECT id, title, text, source_version, source_uri FROM resources "
                        "WHERE id=? AND tenant_id=? AND auth_scope='curriculum'", (resource_id, tenant_id)).fetchone()
    return dict(row) if row else None


def manifest(tenant_id: str):
    with db() as c:
        row = c.execute("SELECT generation, updated_at FROM index_manifests WHERE tenant_id=?", (tenant_id,)).fetchone()
    return dict(row) if row else None


def all_tenants() -> list[str]:
    with db() as c:
        return [r["tenant_id"] for r in c.execute("SELECT tenant_id FROM index_manifests")]


# ------------------------------------------------------------------ audit and feedback
def add_audit(tenant_id: str, user_id: str, action: str, metadata: dict) -> None:
    with db() as c:
        c.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?)", (str(uuid4()), tenant_id, user_id, action, json.dumps(metadata), now()))


def audits(tenant_id: str, limit: int = 50) -> list[dict]:
    with db() as c:
        rows = _rows(c, "SELECT id, user_id, action, metadata, created_at FROM audit_events WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?", (tenant_id, limit))
    for row in rows:
        row["metadata"] = json.loads(row["metadata"])
    return rows


def owns_query(tenant_id: str, user_id: str, request_id: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM audit_events WHERE tenant_id=? AND user_id=? AND action='query' "
                         "AND json_extract(metadata,'$.request_id')=?", (tenant_id, user_id, request_id)).fetchone() is not None


def add_feedback(tenant_id: str, user_id: str, request_id: str, rating: int, comment: str) -> None:
    with db() as c:
        c.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?,?)", (str(uuid4()), tenant_id, user_id, request_id, rating, comment, now()))


# ------------------------------------------------------------------ ingestion jobs
def job_create(tenant_id: str, user_id: str, idempotency_key: str, payload: dict) -> dict:
    """Create a job, or return the existing one for the same key. Raises ValueError on key reuse with other content."""
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with db() as c:
        existing = c.execute("SELECT * FROM ingestion_jobs WHERE tenant_id=? AND idempotency_key=?", (tenant_id, idempotency_key)).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise LookupError("idempotency_conflict")
            return {**dict(existing), "created": False}
        if payload.get("skill_id") and not c.execute("SELECT 1 FROM skills WHERE id=? AND tenant_id=?", (payload["skill_id"], tenant_id)).fetchone():
            raise KeyError("unknown_skill")
        job = {"id": str(uuid4()), "tenant_id": tenant_id, "user_id": user_id, "idempotency_key": idempotency_key,
               "payload_hash": payload_hash, "payload": json.dumps(payload), "status": "queued",
               "resource_id": None, "error": None, "created_at": now()}
        c.execute("INSERT INTO ingestion_jobs VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(job.values()))
    return {**job, "created": True}


def job_get(tenant_id: str, job_id: str, with_payload: bool = False):
    columns = "id, status, resource_id, error, created_at, user_id" + (", payload" if with_payload else "")
    with db() as c:
        row = c.execute(f"SELECT {columns} FROM ingestion_jobs WHERE tenant_id=? AND id=?", (tenant_id, job_id)).fetchone()
    if not row:
        return None
    result = dict(row)
    if with_payload:
        result["payload"] = json.loads(result["payload"])
    return result


def jobs(tenant_id: str, limit: int = 50) -> list[dict]:
    with db() as c:
        return _rows(c, "SELECT id, status, resource_id, error, created_at FROM ingestion_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?", (tenant_id, limit))


def job_finish(tenant_id: str, job_id: str, status: str, resource_id: str | None = None, error: str | None = None) -> None:
    with db() as c:
        c.execute("UPDATE ingestion_jobs SET status=?, resource_id=?, error=? WHERE tenant_id=? AND id=? AND status='queued'",
                  (status, resource_id, error, tenant_id, job_id))


def fail_queued_jobs_on_restart() -> None:
    with db() as c:
        c.execute("UPDATE ingestion_jobs SET status='failed', error='Process restarted before completion; submit a new job.' WHERE status='queued'")


# ------------------------------------------------------------------ publication (two-phase, compare-and-swap)
def publish_prepare(tenant_id: str, documents: list[dict]) -> dict:
    """Validate, identify and link documents. Returns the current generation and which items are new."""
    with db() as c:
        c.execute("INSERT OR IGNORE INTO index_manifests VALUES (?,?,?)", (tenant_id, 0, now()))
        current = c.execute("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant_id,)).fetchone()["generation"]
        skills = _rows(c, "SELECT id, name FROM skills WHERE tenant_id=?", (tenant_id,))
        prepared = []
        for doc in documents:
            title, text = validate(doc["title"], doc["text"])
            resource_id = content_resource_id(tenant_id, title, text)
            exists = c.execute("SELECT 1 FROM resources WHERE id=? AND tenant_id=?", (resource_id, tenant_id)).fetchone()
            prepared.append({
                "id": resource_id, "title": title, "text": text, "skill_id": doc.get("skill_id"),
                "kind": doc.get("kind", "Imported text"),
                "source_uri": doc.get("source_uri") or f"local://imports/{resource_id}",
                "source_version": doc.get("source_version", "v1"),
                "created_at": doc.get("created_at") or now(),
                "entities": extract_entities(text, skills, doc.get("skill_id")),
                "new": exists is None,
            })
    return {"generation": current, "prepared": prepared}


def publish_commit(tenant_id: str, expected: int, target: int, prepared: list[dict], topic_label: str | None) -> bool:
    """Publish in one transaction. Returns False if another publication advanced the generation."""
    with db() as c:
        swapped = c.execute("UPDATE index_manifests SET generation=?, updated_at=? WHERE tenant_id=? AND generation=?",
                            (target, now(), tenant_id, expected))
        if swapped.rowcount != 1:
            return False
        for d in prepared:
            c.execute("INSERT OR IGNORE INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (d["id"], tenant_id, d["title"], d["text"], d["skill_id"], d["kind"], d["source_uri"], d["source_version"], "curriculum", d["created_at"]))
            for skill in d["entities"]:
                c.execute("INSERT OR IGNORE INTO resource_entities VALUES (?,?,?)", (tenant_id, d["id"], skill))
        if topic_label:
            topic_id = "topic-" + hashlib.sha256(f"{tenant_id}|{topic_label.lower()}".encode()).hexdigest()[:16]
            c.execute("INSERT OR IGNORE INTO topics VALUES (?,?,?,?)", (topic_id, tenant_id, topic_label, now()))
            c.executemany("INSERT OR IGNORE INTO topic_resources VALUES (?,?)", [(topic_id, d["id"]) for d in prepared])
    return True


def purge_web(tenant_id: str, cutoff: str):
    """Delete expired web-cache rows atomically. Returns (deleted_ids, generation) or None if nothing expired."""
    for _ in range(MAX_PUBLISH_ATTEMPTS):
        with db() as c:
            c.execute("INSERT OR IGNORE INTO index_manifests VALUES (?,?,?)", (tenant_id, 0, now()))
            current = c.execute("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant_id,)).fetchone()["generation"]
            ids = [r["id"] for r in c.execute("SELECT id FROM resources WHERE tenant_id=? AND kind='Web result' AND created_at<?", (tenant_id, cutoff))]
            if not ids:
                return [], current
            marks = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM resource_entities WHERE resource_id IN ({marks})", ids)
            c.execute(f"DELETE FROM topic_resources WHERE resource_id IN ({marks})", ids)
            c.execute(f"DELETE FROM resources WHERE id IN ({marks})", ids)
            c.execute("DELETE FROM topics WHERE tenant_id=? AND id NOT IN (SELECT topic_id FROM topic_resources)", (tenant_id,))
            swapped = c.execute("UPDATE index_manifests SET generation=?, updated_at=? WHERE tenant_id=? AND generation=?",
                                (current + 1, now(), tenant_id, current))
            if swapped.rowcount == 1:
                return ids, current + 1
    raise RuntimeError("Web cache purge lost repeated generation races")


def index_documents(tenant_id: str) -> dict:
    """Everything needed to rebuild derived indexes for one tenant at its published generation."""
    snap = snapshot(tenant_id, include_web=True)
    return {"generation": snap["generation"],
            "documents": [{"id": r["id"], "title": r["title"], "text": r["text"]} for r in snap["resources"]],
            "support": snap["support"]}


def expired_cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
