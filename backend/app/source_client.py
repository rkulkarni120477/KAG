"""HTTP client for the source service, used by the API and the worker.

`SOURCE_URL` and `SOURCE_SERVICE_TOKEN` configure the connection. Tests may inject an in-process
httpx-compatible client so the same code path runs without a network.
"""
import os
import threading

import httpx


class SourceError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Source service returned {status}: {detail}")
        self.status = status
        self.detail = detail


class SourceClient:
    def __init__(self, http: httpx.Client):
        self.http = http

    @classmethod
    def from_env(cls) -> "SourceClient":
        base = os.getenv("SOURCE_URL", "http://127.0.0.1:8001").rstrip("/")
        token = os.getenv("SOURCE_SERVICE_TOKEN", "")
        return cls(httpx.Client(base_url=base, headers={"X-Service-Token": token}, timeout=10.0))

    def _check(self, response: httpx.Response, allow_missing: bool = False):
        if allow_missing and response.status_code == 404:
            return None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise SourceError(response.status_code, str(detail))
        return response.json()

    def get(self, path, allow_missing=False, **params):
        return self._check(self.http.get(path, params=params), allow_missing)

    def post(self, path, body, allow_missing=False):
        return self._check(self.http.post(path, json=body), allow_missing)

    def post_raw(self, path, body):
        return self.http.post(path, json=body)

    def delete(self, path, **params):
        return self._check(self.http.delete(path, params=params))

    # identity and scope
    def user_identity(self, tenant, user_id):
        return self.get(f"/internal/users/{user_id}", allow_missing=True, tenant=tenant)

    def roster(self, tenant, user_id):
        return self.get("/internal/roster", tenant=tenant, user_id=user_id)["items"]

    def student(self, tenant, student_id):
        return self.get(f"/internal/students/{student_id}", allow_missing=True, tenant=tenant)

    def is_granted(self, tenant, user_id, student_id):
        return self.get(f"/internal/students/{student_id}/granted", tenant=tenant, user_id=user_id)["granted"]

    def progress(self, tenant, student_id):
        return self.get(f"/internal/students/{student_id}/progress", tenant=tenant)["items"]

    def waivers(self, tenant, student_id):
        return self.get(f"/internal/students/{student_id}/waivers", tenant=tenant)["items"]

    def create_waiver(self, tenant, student_id, body):
        return self.post(f"/internal/students/{student_id}/waivers", {"tenant": tenant, **body})

    def revoke_waiver(self, tenant, waiver_id):
        return self.delete(f"/internal/waivers/{waiver_id}", tenant=tenant)

    # curriculum
    def snapshot(self, tenant, include_web=False):
        return self.get("/internal/snapshot", tenant=tenant, include_web=str(include_web).lower())

    def evidence(self, tenant, resource_id):
        return self.get(f"/internal/evidence/{resource_id}", allow_missing=True, tenant=tenant)

    def manifest(self, tenant):
        return self.get("/internal/manifest", tenant=tenant)

    def tenants(self):
        return self.get("/internal/tenants")["items"]

    # audit and feedback
    def add_audit(self, tenant, user_id, action, metadata):
        return self.post("/internal/audit", {"tenant": tenant, "user_id": user_id, "action": action, "metadata": metadata})

    def audits(self, tenant, limit=50):
        return self.get("/internal/audit", tenant=tenant, limit=limit)["items"]

    def add_feedback(self, tenant, user_id, request_id, rating, comment):
        return self.post_raw("/internal/feedback", {"tenant": tenant, "user_id": user_id, "request_id": request_id, "rating": rating, "comment": comment})

    # ingestion jobs
    def create_job(self, tenant, user_id, idempotency_key, payload):
        response = self.post_raw("/internal/jobs", {"tenant": tenant, "user_id": user_id, "idempotency_key": idempotency_key, "payload": payload})
        return response.status_code, self._check(response)

    def job(self, tenant, job_id, with_payload=False):
        return self.get(f"/internal/jobs/{job_id}", allow_missing=True, tenant=tenant, with_payload=str(with_payload).lower())

    def jobs(self, tenant):
        return self.get("/internal/jobs", tenant=tenant)["items"]

    def finish_job(self, tenant, job_id, status, resource_id=None, error=None):
        return self.post(f"/internal/jobs/{job_id}/finish", {"tenant": tenant, "status": status, "resource_id": resource_id, "error": error})

    # publication and maintenance
    def publish_prepare(self, tenant, documents):
        return self.post("/internal/publish/prepare", {"tenant": tenant, "documents": documents})

    def publish_commit(self, tenant, expected, target, prepared, topic_label=None):
        return self.post("/internal/publish/commit", {"tenant": tenant, "expected": expected, "target": target, "prepared": prepared, "topic_label": topic_label})["ok"]

    def purge_web(self, tenant, days):
        return self.post("/internal/web-cache/purge", {"tenant": tenant, "days": days})

    def index_documents(self, tenant):
        return self.get("/internal/index-documents", tenant=tenant)


_lock = threading.Lock()
_client: SourceClient | None = None


def source() -> SourceClient:
    global _client
    with _lock:
        if _client is None:
            _client = SourceClient.from_env()
        return _client


def set_source(client: SourceClient | None) -> None:
    """Install a client (tests use an in-process one). Pass None to reset to the environment default."""
    global _client
    with _lock:
        _client = client
