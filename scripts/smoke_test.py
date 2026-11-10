"""Smoke-test the running deployment through the Next.js reverse proxy.

Run: .venv/Scripts/python.exe scripts/smoke_test.py
Uses synthetic accounts and creates one idempotent verification resource.
"""
import os
import re

import httpx

base = os.getenv("KAG_WEB_URL", "http://127.0.0.1:3000")
password = os.getenv("KAG_DEMO_PASSWORD", "demo-pass-2026")


def run():
    with httpx.Client(base_url=base, timeout=60, trust_env=False) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Northstar" in page.text
        assets = set(re.findall(r'(?:src|href)="([^" ]+\.(?:js|css)(?:\?[^" ]*)?)"',page.text))
        for asset in assets:
            assert client.get(asset).status_code == 200, asset
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/overview").status_code == 401
        for role,count in [("student",1),("teacher",2),("advisor",1),("admin",0)]:
            response = client.post("/api/v1/auth/login",json={"email":f"{role}@northstar.demo","password":password})
            assert response.status_code == 200, response.text
            assert "httponly" in response.headers["set-cookie"].lower()
            overview = client.get("/api/v1/overview").json()
            assert len(overview["students"]) == count
            assert client.get("/api/v1/resources").status_code == 200
            assert client.get("/api/v1/curriculum/standards").status_code == 200
            graph = client.get("/api/v1/knowledge/graph").json()
            assert graph["nodes"] and graph["edges"]
            if role == "student":
                assert client.get("/api/v1/students/jamie/learning-path").status_code == 404
                assert client.get("/api/v1/admin/audit").status_code == 403
                with client.stream("POST","/api/v1/kag/query",json={"question":"Which mathematics topics should I study before Algebra I?","student_id":"alex","stream":True}) as stream:
                    assert stream.status_code == 200
                    body = stream.read().decode()
                assert "event: status" in body and "event: result" in body and "event: done" in body
                assert "resource-" in body and "advisory_only" in body
            if role == "admin":
                assert client.get("/api/v1/students/alex/learning-path").status_code == 404
                payload = {"title":"Verification: equivalent expressions","text":"Equivalent expressions have the same value for every permitted value of the variable. Use the distributive property to rewrite 3(x + 2) as 3x + 6. Synthetic verification resource.","idempotency_key":"running-deployment-smoke-v1"}
                response = client.post("/api/v1/admin/ingestion",json=payload)
                assert response.status_code == 202, response.text
                # Background task has normally completed by the next response.
                job = client.get("/api/v1/admin/ingestion/"+response.json()["id"]).json()
                assert job["status"] == "completed", job
                assert client.get("/api/v1/evidence/"+job["resource_id"]).status_code == 200
            assert client.post("/api/v1/auth/logout").status_code == 200
            assert client.get("/api/v1/auth/me").status_code == 401
    print(f"PASS: {base} — page/assets, health, four roles, session cookies, access denials, curriculum, graph, streaming query, and persistent ingestion.")


if __name__ == "__main__":
    run()
