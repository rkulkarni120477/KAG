"""Source-service database. Only this process opens education.db; everything else calls its API."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event

DATA_DIR = Path(os.getenv("KAG_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DATA_DIR / 'education.db'}", connect_args={"check_same_thread": False, "timeout": 15})


@event.listens_for(engine, "connect")
def configure(connection, _):
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")


@contextmanager
def db():
    connection = engine.raw_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS students (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, grade INTEGER NOT NULL, user_id TEXT);
CREATE TABLE IF NOT EXISTS access_grants (user_id TEXT NOT NULL, student_id TEXT NOT NULL, PRIMARY KEY(user_id,student_id));
CREATE TABLE IF NOT EXISTS courses (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS prerequisite_rules (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    skill_id TEXT NOT NULL REFERENCES skills(id), group_id TEXT NOT NULL,
    logic TEXT NOT NULL CHECK (logic IN ('ALL','ANY')),
    requires_id TEXT NOT NULL REFERENCES skills(id),
    effective_from TEXT, effective_until TEXT,
    UNIQUE(tenant_id, skill_id, group_id, requires_id));
CREATE TABLE IF NOT EXISTS curriculum_standards (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL, grade INTEGER NOT NULL, description TEXT NOT NULL, source_uri TEXT NOT NULL, source_version TEXT NOT NULL, illustrative INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, skill_id TEXT, kind TEXT NOT NULL, source_uri TEXT NOT NULL, source_version TEXT NOT NULL, auth_scope TEXT NOT NULL DEFAULT 'curriculum', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assessments (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, skill_id TEXT NOT NULL REFERENCES skills(id));
CREATE TABLE IF NOT EXISTS student_progress (student_id TEXT NOT NULL, skill_id TEXT NOT NULL, assessment_id TEXT REFERENCES assessments(id), score INTEGER, assessed_at TEXT, PRIMARY KEY(student_id,skill_id));
CREATE TABLE IF NOT EXISTS waivers (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, student_id TEXT NOT NULL REFERENCES students(id),
    skill_id TEXT NOT NULL REFERENCES skills(id), reason TEXT NOT NULL, granted_by TEXT NOT NULL,
    effective_from TEXT NOT NULL, effective_until TEXT, revoked_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS academic_policies (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, source_version TEXT NOT NULL, threshold INTEGER);
CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, request_id TEXT NOT NULL, rating INTEGER NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, action TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ingestion_jobs (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, resource_id TEXT, error TEXT, created_at TEXT NOT NULL, UNIQUE(tenant_id,idempotency_key));
CREATE TABLE IF NOT EXISTS index_manifests (tenant_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(tenant_id,label));
CREATE TABLE IF NOT EXISTS topic_resources (topic_id TEXT NOT NULL REFERENCES topics(id), resource_id TEXT NOT NULL REFERENCES resources(id), PRIMARY KEY(topic_id,resource_id));
CREATE TABLE IF NOT EXISTS resource_entities (tenant_id TEXT NOT NULL, resource_id TEXT NOT NULL REFERENCES resources(id), skill_id TEXT NOT NULL REFERENCES skills(id), PRIMARY KEY(resource_id,skill_id));
CREATE INDEX IF NOT EXISTS resources_tenant ON resources(tenant_id);
CREATE INDEX IF NOT EXISTS audit_tenant ON audit_events(tenant_id,created_at);
CREATE INDEX IF NOT EXISTS waivers_student ON waivers(tenant_id,student_id);
"""


def initialize() -> None:
    with db() as connection:
        connection.executescript(SCHEMA)
