"""Integration tests exercise real SQLite, orchestration, sessions and API policy."""
import asyncio
import os
import tempfile

# Isolate test records from the running development application.
os.environ["KAG_DATA_DIR"] = tempfile.mkdtemp(prefix="northstar-test-")
os.environ["KAG_LOAD_DOTENV"] = "false"
os.environ.pop("BEDROCK_MODEL_ID", None)

import httpx

from app.main import app
from app.db import db
from app.security import new_session


def test_end_to_end_and_access_controls():
    asyncio.run(scenario())


async def scenario():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/v1/health")).status_code == 200
            assert (await client.get("/api/v1/overview")).status_code == 401
            assert (await client.post("/api/v1/auth/login", json={"email":"student@northstar.demo","password":"incorrect"})).status_code == 401
            result = await client.post("/api/v1/auth/login", json={"email":"student@northstar.demo","password":"demo-pass-2026"})
            assert result.status_code == 200
            assert "httponly" in result.headers["set-cookie"].lower()
            overview = (await client.get("/api/v1/overview")).json()
            assert [s["id"] for s in overview["students"]] == ["alex"]
            assert (await client.get("/api/v1/students/jamie/learning-path")).status_code == 404
            assert (await client.get("/api/v1/students/outside-student/learning-path")).status_code == 404
            assert (await client.get("/api/v1/evidence/isolation-secret")).status_code == 404
            assert (await client.get("/api/v1/admin/audit")).status_code == 403
            path = (await client.get("/api/v1/students/alex/learning-path")).json()
            assert path["mastered"] == 1
            by_id = {s["id"]: s for s in path["steps"]}
            assert by_id["ratios"]["status"] == "unknown"
            assert by_id["expressions"]["status"] == "not_satisfied"
            positions = {s["id"]:i for i,s in enumerate(path["steps"])}
            for step in path["steps"]:
                for required in step["prerequisites"]:
                    assert positions[required] < positions[step["id"]]
            query = await client.post("/api/v1/kag/query", json={"question":"Which topics should I study before Algebra I?","student_id":"alex"})
            assert query.status_code == 200, query.text
            answer = query.json()
            assert len(answer["evidence"]) > 0
            assert answer["advisory_only"] is True
            assert "isolation-secret" not in str(answer)
            assert answer["generator"] == "Evidence-only local mode"
            assert (await client.post("/api/v1/feedback", json={"request_id":answer["request_id"],"rating":5})).status_code == 201
            feedback_request_id = answer["request_id"]
            assert (await client.post("/api/v1/feedback", json={"request_id":"not-owned","rating":5})).status_code == 404
            assert (await client.post("/api/v1/kag/query", json={"question":"Algebra I","student_id":"jamie"})).status_code == 404
            stream = await client.post("/api/v1/kag/query", json={"question":"Explain equations","stream":True})
            assert stream.status_code == 200
            assert "event: result" in stream.text and "event: done" in stream.text
            irrelevant = (await client.post("/api/v1/kag/query", json={"question":"xylophone astrophysics bananas"})).json()
            assert irrelevant["completeness"] == "insufficient"
            assert (await client.post("/api/v1/auth/logout",headers={"Origin":"https://malicious.example"})).status_code == 403
            assert (await client.post("/api/v1/auth/logout")).status_code == 200
            assert (await client.get("/api/v1/overview")).status_code == 401

            await client.post("/api/v1/auth/login", json={"email":"teacher@northstar.demo","password":"demo-pass-2026"})
            assert len((await client.get("/api/v1/overview")).json()["students"]) == 2
            assert (await client.get("/api/v1/students/jamie/learning-path")).json()["mastered"] == 4
            await client.post("/api/v1/auth/logout")

            await client.post("/api/v1/auth/login", json={"email":"admin@northstar.demo","password":"demo-pass-2026"})
            assert (await client.get("/api/v1/overview")).json()["students"] == []
            assert (await client.get("/api/v1/students/alex/learning-path")).status_code == 404
            payload = {"title":"Synthetic geometry resource","text":"Triangles have three sides. This synthetic geometry resource is used to verify ingestion and retrieval.","idempotency_key":"integration-import-001"}
            imported = await client.post("/api/v1/admin/ingestion",json=payload)
            assert imported.status_code == 202, imported.text
            job = (await client.get("/api/v1/admin/ingestion/" + imported.json()["id"])).json()
            assert job["status"] == "completed", job
            imported_job_id = job["id"]
            replay = await client.post("/api/v1/admin/ingestion",json=payload)
            assert replay.json()["id"] == job["id"]
            conflict = await client.post("/api/v1/admin/ingestion",json={**payload,"text":"Completely different content, long enough to pass validation."})
            assert conflict.status_code == 409
            search = (await client.get("/api/v1/resources?q=Triangles")).json()
            assert len(search["items"]) == 1
            imported_answer = (await client.post("/api/v1/kag/query",json={"question":"Tell me about triangles"})).json()
            assert job["resource_id"] in [e["id"] for e in imported_answer["evidence"]]
            assert (await client.get("/api/v1/admin/audit")).status_code == 200

            # A genuine second-tenant session still cannot fetch Northstar evidence.
            client.cookies.set("kag_session",new_session("outsider"),domain="test.local",path="/api")
            assert (await client.get("/api/v1/evidence/course-outline")).status_code == 404
            assert (await client.get("/api/v1/evidence/isolation-secret")).status_code == 200

        with db() as connection:
            # Counts are scoped to this test's records; other test modules share the database.
            assert connection.execute("SELECT COUNT(*) FROM feedback WHERE request_id=?", (feedback_request_id,)).fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE id=?", (imported_job_id,)).fetchone()[0] == 1
            # Raw prompts are deliberately absent from audit records.
            assert "Which topics" not in " ".join(r["metadata"] for r in connection.execute("SELECT metadata FROM audit_events"))
