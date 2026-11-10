"""End-to-end API behaviour through Keycloak-issued tokens, the source service and the worker pipeline."""
import asyncio
import os

import httpx

from app.main import app
from helpers import mint, query_all, query_one


def run(scenario):
    asyncio.run(scenario())


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def login(c, email, password="demo-pass-2026"):
    return await c.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_end_to_end_and_access_controls():
    async def scenario():
        async with client() as c:
            assert (await c.get("/api/v1/health")).status_code == 200
            assert (await c.get("/api/v1/overview")).status_code == 401
            assert (await login(c, "student@northstar.demo", "incorrect")).status_code == 401
            result = await login(c, "student@northstar.demo")
            assert result.status_code == 200, result.text
            assert "httponly" in result.headers["set-cookie"].lower()
            assert (await c.get("/api/v1/auth/me")).json()["role"] == "student"
            overview = (await c.get("/api/v1/overview")).json()
            assert [s["id"] for s in overview["students"]] == ["alex"]
            assert (await c.get("/api/v1/students/jamie/learning-path")).status_code == 404
            assert (await c.get("/api/v1/students/outside-student/learning-path")).status_code == 404
            assert (await c.get("/api/v1/evidence/isolation-secret")).status_code == 404
            assert (await c.get("/api/v1/admin/audit")).status_code == 403
            path = (await c.get("/api/v1/students/alex/learning-path")).json()
            assert path["mastered"] == 1
            by_id = {s["id"]: s for s in path["steps"]}
            assert by_id["ratios"]["status"] == "unknown"
            assert by_id["expressions"]["status"] == "not_satisfied"
            positions = {s["id"]: i for i, s in enumerate(path["steps"])}
            for step in path["steps"]:
                for required in step["prerequisites"]:
                    assert positions[required] < positions[step["id"]]
            query_response = await c.post("/api/v1/kag/query", json={"question": "Which topics should I study before Algebra I?", "student_id": "alex"})
            assert query_response.status_code == 200, query_response.text
            answer = query_response.json()
            assert len(answer["evidence"]) > 0
            assert answer["advisory_only"] is True
            assert "isolation-secret" not in str(answer)
            assert answer["generator"] == "Evidence-only local mode"
            assert (await c.post("/api/v1/feedback", json={"request_id": answer["request_id"], "rating": 5})).status_code == 201
            assert (await c.post("/api/v1/feedback", json={"request_id": "not-owned", "rating": 5})).status_code == 404
            assert (await c.post("/api/v1/kag/query", json={"question": "Algebra I", "student_id": "jamie"})).status_code == 404
            stream = await c.post("/api/v1/kag/query", json={"question": "Explain equations", "stream": True})
            assert stream.status_code == 200
            assert "event: result" in stream.text and "event: done" in stream.text
            irrelevant = (await c.post("/api/v1/kag/query", json={"question": "xylophone astrophysics bananas"})).json()
            assert irrelevant["completeness"] == "insufficient"
            assert (await c.post("/api/v1/auth/logout", headers={"Origin": "https://malicious.example"})).status_code == 403
            assert (await c.post("/api/v1/auth/logout")).status_code == 200

            # Teacher: instruction purpose on two students.
            assert (await login(c, "teacher@northstar.demo")).status_code == 200
            assert len((await c.get("/api/v1/overview")).json()["students"]) == 2
            assert (await c.get("/api/v1/students/jamie/learning-path")).json()["mastered"] == 4

            # Administrator: no student-record purpose, but may run ingestion.
            assert (await login(c, "admin@northstar.demo")).status_code == 200
            assert (await c.get("/api/v1/overview")).json()["students"] == []
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
            payload = {"title": "Synthetic geometry resource", "text": "Triangles have three sides. This synthetic geometry resource is used to verify ingestion and retrieval.", "idempotency_key": "integration-import-001"}
            imported = await c.post("/api/v1/admin/ingestion", json=payload)
            assert imported.status_code == 202, imported.text
            job = (await c.get("/api/v1/admin/ingestion/" + imported.json()["id"])).json()
            assert job["status"] == "completed", job
            replay = await c.post("/api/v1/admin/ingestion", json=payload)
            assert replay.json()["id"] == job["id"]
            conflict = await c.post("/api/v1/admin/ingestion", json={**payload, "text": "Completely different content, long enough to pass validation."})
            assert conflict.status_code == 409
            search = (await c.get("/api/v1/resources?q=Triangles")).json()
            assert len(search["items"]) == 1
            imported_answer = (await c.post("/api/v1/kag/query", json={"question": "Tell me about triangles"})).json()
            assert job["resource_id"] in [e["id"] for e in imported_answer["evidence"]]
            assert (await c.get("/api/v1/admin/audit")).status_code == 200

            # A genuine second-tenant session still cannot fetch Northstar evidence.
            outsider = {"Cookie": f"kag_session={mint('outsider', 'other', 'teacher')}"}
            assert (await c.get("/api/v1/evidence/course-outline", headers=outsider)).status_code == 404
            assert (await c.get("/api/v1/evidence/isolation-secret", headers=outsider)).status_code == 200

        feedback_rows = query_all("SELECT id FROM feedback WHERE tenant_id='northstar'")
        assert len(feedback_rows) >= 1
        audit_text = " ".join(r["metadata"] for r in query_all("SELECT metadata FROM audit_events WHERE tenant_id='northstar'"))
        assert "Which topics" not in audit_text  # raw prompts are never written to audit records
    run(scenario)
