"""The source service accepts only its service token and exposes tenant-scoped data only."""
import httpx

from app.source.main import app as source_app
from fastapi.testclient import TestClient


def test_source_rejects_data_requests_without_the_service_token():
    with TestClient(source_app, base_url="http://source.test") as bare:
        assert bare.get("/internal/health").status_code == 200  # liveness only, no data
        assert bare.get("/internal/snapshot", params={"tenant": "northstar"}).status_code == 401
        assert bare.get("/internal/snapshot", params={"tenant": "northstar"}, headers={"X-Service-Token": "wrong"}).status_code == 401


def test_source_health_with_the_service_token(source_token):
    with TestClient(source_app, base_url="http://source.test") as authed:
        response = authed.get("/internal/health", headers={"X-Service-Token": source_token})
        assert response.status_code == 200 and response.json()["service"] == "source"


def test_snapshot_is_scoped_to_the_requested_tenant(source_token):
    with TestClient(source_app, base_url="http://source.test") as authed:
        headers = {"X-Service-Token": source_token}
        northstar = authed.get("/internal/snapshot", params={"tenant": "northstar"}, headers=headers).json()
        other = authed.get("/internal/snapshot", params={"tenant": "other"}, headers=headers).json()
    assert all(r["id"] != "isolation-secret" for r in northstar["resources"])
    assert {s["id"] for s in other["skills"]}.isdisjoint({s["id"] for s in northstar["skills"]})


def test_source_refuses_feedback_for_queries_the_user_did_not_make(source_token):
    with TestClient(source_app, base_url="http://source.test") as authed:
        response = authed.post("/internal/feedback", headers={"X-Service-Token": source_token},
                               json={"tenant": "northstar", "user_id": "student", "request_id": "not-a-query", "rating": 3})
    assert response.status_code == 404
