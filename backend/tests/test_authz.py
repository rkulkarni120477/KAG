"""Authorization: stale grants, fail-closed policy decisions, tenant isolation and citation dereference."""
import asyncio
import os
import tempfile
from uuid import uuid4

os.environ.setdefault("KAG_DATA_DIR", tempfile.mkdtemp(prefix="kag-authz-test-"))
os.environ["KAG_LOAD_DOTENV"] = "false"
os.environ["WEB_SEARCH_ENABLED"] = "false"
os.environ.pop("OPA_URL", None)

import httpx
import pytest

from app.db import db
from app.ingestion import publish
from app.main import app
from app.policy import allow_student_access
from app.security import new_session


def run(scenario):
    async def wrapper():
        async with app.router.lifespan_context(app):
            await scenario()
    asyncio.run(wrapper())


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def login(c, email):
    response = await c.post("/api/v1/auth/login", json={"email": email, "password": "demo-pass-2026"})
    assert response.status_code == 200


def test_revoked_grant_takes_effect_on_the_next_request():
    async def scenario():
        async with client() as c:
            await login(c, "teacher@northstar.demo")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 200
            with db() as conn:
                conn.execute("DELETE FROM access_grants WHERE user_id='teacher' AND student_id='alex'")
            try:
                assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
                assert (await c.post("/api/v1/kag/query", json={"question": "Algebra I", "student_id": "alex"})).status_code == 404
            finally:
                with db() as conn:
                    conn.execute("INSERT OR IGNORE INTO access_grants VALUES ('teacher','alex')")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 200
    run(scenario)


def test_policy_service_outage_fails_closed(monkeypatch):
    async def scenario():
        async with client() as c:
            await login(c, "teacher@northstar.demo")
            monkeypatch.setenv("OPA_URL", "http://127.0.0.1:9")  # nothing listens here
            try:
                assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
            finally:
                monkeypatch.delenv("OPA_URL")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 200
    run(scenario)


def test_local_policy_requires_grant_and_same_tenant():
    teacher = {"id": "teacher", "tenant_id": "northstar", "role": "teacher"}
    assert allow_student_access(teacher, {"id": "alex", "tenant_id": "northstar"}, True) is True
    assert allow_student_access(teacher, {"id": "alex", "tenant_id": "northstar"}, False) is False
    assert allow_student_access(teacher, {"id": "private", "tenant_id": "other"}, True) is False


def test_another_tenants_session_cannot_reach_northstar_records_or_citations():
    async def scenario():
        async with client() as c:
            cookie = {"Cookie": f"kag_session={new_session('outsider')}"}
            assert (await c.get("/api/v1/students/alex/learning-path", headers=cookie)).status_code == 404
            assert (await c.get("/api/v1/evidence/resource-ratios", headers=cookie)).status_code == 404
            assert (await c.get("/api/v1/evidence/course-outline", headers=cookie)).status_code == 404
            assert (await c.get("/api/v1/evidence/isolation-secret", headers=cookie)).status_code == 200  # own tenant
    run(scenario)


def test_citation_dereference_is_scoped_to_the_callers_tenant():
    tenant = f"lab-authz-{uuid4().hex[:8]}"
    resource_id = publish(tenant, [{"title": "Private lab note", "text": "A note that only the lab tenant may read. Synthetic."}])["resource_ids"][0]

    async def scenario():
        async with client() as c:
            await login(c, "teacher@northstar.demo")
            assert (await c.get(f"/api/v1/evidence/{resource_id}")).status_code == 404
            answer = (await c.post("/api/v1/kag/query", json={"question": "private lab note"})).json()
            assert resource_id not in [e["id"] for e in answer["evidence"]]
    run(scenario)


def test_ids_for_missing_and_foreign_students_are_indistinguishable():
    async def scenario():
        async with client() as c:
            await login(c, "teacher@northstar.demo")
            missing = await c.get("/api/v1/students/does-not-exist/learning-path")
            foreign = await c.get("/api/v1/students/outside-student/learning-path")
            assert missing.status_code == foreign.status_code == 404
            assert missing.json() == foreign.json()
    run(scenario)
