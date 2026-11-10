"""Internal source API. The only process that opens education.db.

Callers (API and worker) must present the service token. Production replaces the shared token with
workload identity (mTLS or signed service tokens) and enforces the caller's delegated scope here.
"""
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import repository as repo
from .db import initialize
from .seed import seed


def require_service_token(x_service_token: str = Header(default="")) -> None:
    expected = os.getenv("SOURCE_SERVICE_TOKEN", "")
    if not expected or not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(401, "Service authentication failed")


@asynccontextmanager
async def lifespan(_):
    initialize()
    seed()
    repo.fail_queued_jobs_on_restart()
    yield


app = FastAPI(title="Northstar source service", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
# Every data route requires the service token. Liveness is public and returns no data.
api = APIRouter(dependencies=[Depends(require_service_token)])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WaiverIn(Strict):
    tenant: str
    skill_id: str
    reason: str = Field(min_length=3, max_length=500)
    granted_by: str
    effective_from: str
    effective_until: str | None = None


class AuditIn(Strict):
    tenant: str
    user_id: str
    action: str
    metadata: dict = {}


class FeedbackIn(Strict):
    tenant: str
    user_id: str
    request_id: str
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class JobIn(Strict):
    tenant: str
    user_id: str
    idempotency_key: str
    payload: dict


class JobFinishIn(Strict):
    tenant: str
    status: str
    resource_id: str | None = None
    error: str | None = None


class PrepareIn(Strict):
    tenant: str
    documents: list[dict]


class CommitIn(Strict):
    tenant: str
    expected: int
    target: int
    prepared: list[dict]
    topic_label: str | None = None


class PurgeIn(Strict):
    tenant: str
    days: int = Field(ge=1, le=3650)


@app.get("/internal/health")
def health():
    return {"status": "ok", "service": "source"}





@api.get("/internal/users/{user_id}")
def user(user_id: str, tenant: str):
    row = repo.user_identity(tenant, user_id)
    if not row:
        raise HTTPException(404, "User not found in tenant")
    return row


@api.get("/internal/roster")
def roster(tenant: str, user_id: str):
    return {"items": repo.roster(tenant, user_id)}


@api.get("/internal/students/{student_id}")
def student(student_id: str, tenant: str):
    row = repo.student(tenant, student_id)
    if not row:
        raise HTTPException(404, "Student not found")
    return row


@api.get("/internal/students/{student_id}/granted")
def granted(student_id: str, tenant: str, user_id: str):
    return {"granted": repo.is_granted(tenant, user_id, student_id)}


@api.get("/internal/students/{student_id}/progress")
def progress(student_id: str, tenant: str):
    return {"items": repo.progress(tenant, student_id)}


@api.get("/internal/students/{student_id}/waivers")
def list_waivers(student_id: str, tenant: str):
    return {"items": repo.waivers(tenant, student_id)}


@api.post("/internal/students/{student_id}/waivers", status_code=201)
def create_waiver(student_id: str, data: WaiverIn):
    if not repo.student(data.tenant, student_id):
        raise HTTPException(404, "Student not found")
    return repo.add_waiver(data.tenant, student_id, data.skill_id, data.reason, data.granted_by, data.effective_from, data.effective_until)


@api.delete("/internal/waivers/{waiver_id}")
def revoke(waiver_id: str, tenant: str):
    if not repo.revoke_waiver(tenant, waiver_id):
        raise HTTPException(404, "Active waiver not found")
    return {"revoked": True}


@api.get("/internal/snapshot")
def snapshot(tenant: str, include_web: bool = False):
    return repo.snapshot(tenant, include_web)


@api.get("/internal/evidence/{resource_id}")
def evidence(resource_id: str, tenant: str):
    row = repo.evidence(tenant, resource_id)
    if not row:
        raise HTTPException(404, "Evidence not found")
    return row


@api.get("/internal/manifest")
def manifest(tenant: str):
    return repo.manifest(tenant) or {"generation": 0, "updated_at": None}


@api.get("/internal/tenants")
def tenants():
    return {"items": repo.all_tenants()}


@api.post("/internal/audit", status_code=201)
def audit(data: AuditIn):
    repo.add_audit(data.tenant, data.user_id, data.action, data.metadata)
    return {"ok": True}


@api.get("/internal/audit")
def audit_list(tenant: str, limit: int = Query(default=50, le=200)):
    return {"items": repo.audits(tenant, limit)}


@api.post("/internal/feedback", status_code=201)
def feedback(data: FeedbackIn):
    if not repo.owns_query(data.tenant, data.user_id, data.request_id):
        raise HTTPException(404, "Query not found")
    repo.add_feedback(data.tenant, data.user_id, data.request_id, data.rating, data.comment)
    return {"saved": True}


@api.post("/internal/jobs", status_code=202)
def create_job(data: JobIn):
    try:
        job = repo.job_create(data.tenant, data.user_id, data.idempotency_key, data.payload)
    except LookupError:
        raise HTTPException(409, "Idempotency key already used with different content")
    except KeyError:
        raise HTTPException(422, "Unknown skill")
    return {k: job[k] for k in ("id", "status", "created", "resource_id", "error", "created_at")}


@api.get("/internal/jobs")
def list_jobs(tenant: str):
    return {"items": repo.jobs(tenant)}


@api.get("/internal/jobs/{job_id}")
def get_job(job_id: str, tenant: str, with_payload: bool = False):
    job = repo.job_get(tenant, job_id, with_payload)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@api.post("/internal/jobs/{job_id}/finish")
def finish_job(job_id: str, data: JobFinishIn):
    repo.job_finish(data.tenant, job_id, data.status, data.resource_id, data.error)
    return {"ok": True}


@api.post("/internal/publish/prepare")
def publish_prepare(data: PrepareIn):
    try:
        return repo.publish_prepare(data.tenant, data.documents)
    except ValueError as error:
        raise HTTPException(422, str(error))


@api.post("/internal/publish/commit")
def publish_commit(data: CommitIn):
    return {"ok": repo.publish_commit(data.tenant, data.expected, data.target, data.prepared, data.topic_label)}


@api.post("/internal/web-cache/purge")
def purge(data: PurgeIn):
    ids, generation = repo.purge_web(data.tenant, repo.expired_cutoff(data.days))
    return {"ids": ids, "generation": generation}


@api.get("/internal/index-documents")
def index_documents(tenant: str):
    return repo.index_documents(tenant)


app.include_router(api)
