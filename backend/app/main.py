"""Northstar API. No process-local data store: state lives in the source service, Redis and Keycloak."""
import json
import logging
import os
import time
from datetime import datetime
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from . import auth, runtime
from .contracts import (FeedbackRequest, IngestRequest, LoginRequest, QueryRequest, QueryResponse,
                        RecommendationRequest, User, WaiverRequest)
from .kag import workflow
from .reasoning import knowledge_graph, learning_path
from .security import admin, current_user, student_access
from .source_client import SourceError, source
from .worker.tasks import submit_ingest

log = logging.getLogger("kag")

app = FastAPI(title="Northstar Educational KAG", version="2.0.0")
origins = set(os.getenv("KAG_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))
LOGIN_FAILURE_LIMIT = 10


def _raise_from(error: SourceError):
    raise HTTPException(error.status, error.detail)


def audit(user: dict, action: str, metadata: dict | None = None) -> None:
    source().add_audit(user["tenant_id"], user["id"], action, metadata or {})


@app.middleware("http")
async def request_controls(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and origin and origin not in origins:
        return JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
    ip = request.client.host if request.client else "unknown"
    is_login = request.url.path.endswith("/auth/login")
    if is_login:
        allowed = runtime.allow(f"login:{ip}", int(os.getenv("KAG_RATE_LIMIT_LOGIN", "30")))
    else:
        allowed = runtime.allow(f"api:{ip}", int(os.getenv("KAG_RATE_LIMIT_API", "180")))
    if not allowed:
        return JSONResponse({"detail": "Too many requests. Please try again shortly."}, status_code=429, headers={"Retry-After": "60"})
    request_id = str(uuid4())
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f'app;dur={(time.monotonic() - started) * 1000:.1f}'
    return response


# ------------------------------------------------------------------ authentication
@app.post("/api/v1/auth/login", response_model=User)
def login(data: LoginRequest, response: Response):
    email = data.email.lower()
    if runtime.failure_count(email) >= LOGIN_FAILURE_LIMIT:
        raise HTTPException(429, "Too many failed sign-in attempts. Try again later.")
    try:
        token = auth.password_grant(email, data.password)
    except auth.IdentityProviderUnavailable:
        raise HTTPException(503, "Sign-in is temporarily unavailable.")
    if not token:
        runtime.record_failure(email)
        raise HTTPException(401, "Email or password is incorrect")
    try:
        claims = auth.verify(token)
    except auth.TokenError:
        raise HTTPException(401, "Email or password is incorrect")
    identity = source().user_identity(claims["tenant_id"], claims["kag_user_id"])
    if not identity or identity["role"] != claims["role"]:
        raise HTTPException(401, "Email or password is incorrect")
    runtime.clear_failures(email)
    lifetime = max(60, int(claims["exp"] - time.time()))
    response.set_cookie("kag_session", token, max_age=lifetime, httponly=True,
                        secure=os.getenv("KAG_COOKIE_SECURE", "false") == "true", samesite="lax", path="/api")
    user = {k: identity[k] for k in ("id", "tenant_id", "name", "email", "role")}
    audit(user, "login")
    return user


@app.post("/api/v1/auth/logout")
def logout(response: Response, user=Depends(current_user)):
    # Access tokens are short-lived and not revocable in this pilot; the cookie is removed.
    response.delete_cookie("kag_session", path="/api")
    return {"ok": True}


@app.get("/api/v1/auth/me", response_model=User)
def me(user=Depends(current_user)):
    return user


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "application": "Northstar KAG", "demo": True}


@app.get("/api/v1/ready")
def ready():
    """Readiness: the source service and the configured identity provider must answer."""
    source().get("/internal/health")
    return {"status": "ready"}


# ------------------------------------------------------------------ overview and curriculum
@app.get("/api/v1/overview")
def overview(user=Depends(current_user)):
    tenant = user["tenant_id"]
    snap = source().snapshot(tenant, include_web=False)
    queries = sum(1 for a in source().audits(tenant, limit=200) if a["action"] == "query" and a["user_id"] == user["id"])
    return {
        "user": user,
        "students": source().roster(tenant, user["id"]),
        "stats": {"resources": len(snap["resources"]), "standards": len(snap["standards"]), "skills": len(snap["skills"]), "queries": queries},
        "manifest": source().manifest(tenant),
        "bedrock_configured": bool(os.getenv("BEDROCK_MODEL_ID")),
        "bedrock_model_id": os.getenv("BEDROCK_MODEL_ID") or None,
        "bedrock_region": os.getenv("AWS_REGION", "us-east-1"),
        "retrieval": f"Hybrid BM25 + chunk vectors ({os.getenv('KAG_VECTOR_BACKEND', 'sqlite')})",
        "embeddings": "Bedrock" if os.getenv("BEDROCK_EMBED_MODEL_ID") else "Hashed (offline, not a trained model)",
        "graph": f"Source service + NetworkX; {os.getenv('KAG_GRAPH_BACKEND', 'sqlite')} index",
        "queue": "Redis + RQ" if runtime.queue_mode() == "redis" else "inline",
        "demo": True,
    }


@app.get("/api/v1/curriculum/standards")
def standards(q: str = "", grade: int | None = None, state: str | None = None, user=Depends(current_user)):
    rows = source().snapshot(user["tenant_id"], include_web=False)["standards"]
    return {"items": [r for r in rows if (not q or q.lower() in (r["title"] + r["description"]).lower())
                      and (grade is None or r["grade"] == grade) and (not state or r["state"] == state)],
            "notice": "DEMO identifiers are illustrative; they are not official state-standard codes."}


@app.get("/api/v1/resources")
def resources(q: str = "", user=Depends(current_user)):
    rows = source().snapshot(user["tenant_id"], include_web=False)["resources"]
    return {"items": [r for r in rows if not q or q.lower() in (r["title"] + r["text"]).lower()]}


@app.get("/api/v1/evidence/{evidence_id}")
def evidence(evidence_id: str, user=Depends(current_user)):
    row = source().evidence(user["tenant_id"], evidence_id)
    if not row:
        raise HTTPException(404, "Evidence not found")
    return row


@app.get("/api/v1/knowledge/graph")
def graph(user=Depends(current_user)):
    return knowledge_graph(user)


# ------------------------------------------------------------------ learning paths and rosters
def _as_of(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "as_of must be an ISO 8601 date or timestamp")


@app.get("/api/v1/students/{student_id}/learning-path")
def path(student_id: str, as_of: str | None = None, user=Depends(current_user)):
    student_access(user, student_id)
    try:
        return learning_path(user, student_id, _as_of(as_of))
    except ValueError as error:
        raise HTTPException(409, str(error))


@app.get("/api/v1/roster")
def roster(user=Depends(current_user)):
    return {"items": source().roster(user["tenant_id"], user["id"])}


@app.post("/api/v1/students/{student_id}/waivers", status_code=201)
def create_waiver(student_id: str, data: WaiverRequest, user=Depends(current_user)):
    if user["role"] != "teacher":
        raise HTTPException(403, "Only teachers can record a waiver")
    student_access(user, student_id, purpose="instruction")
    if not any(s["id"] == data.skill_id for s in source().snapshot(user["tenant_id"], include_web=False)["skills"]):
        raise HTTPException(422, "Unknown skill")
    try:
        waiver = source().create_waiver(user["tenant_id"], student_id, {
            "skill_id": data.skill_id, "reason": data.reason, "granted_by": user["id"],
            "effective_from": data.effective_from, "effective_until": data.effective_until})
    except SourceError as error:
        _raise_from(error)
    audit(user, "waiver_granted", {"student_id": student_id, "skill_id": data.skill_id, "waiver_id": waiver["id"]})
    return waiver


@app.get("/api/v1/students/{student_id}/waivers")
def list_waivers(student_id: str, user=Depends(current_user)):
    student_access(user, student_id)
    return {"items": source().waivers(user["tenant_id"], student_id)}


@app.delete("/api/v1/students/{student_id}/waivers/{waiver_id}")
def revoke_waiver(student_id: str, waiver_id: str, user=Depends(current_user)):
    if user["role"] != "teacher":
        raise HTTPException(403, "Only teachers can revoke a waiver")
    student_access(user, student_id, purpose="instruction")
    try:
        source().revoke_waiver(user["tenant_id"], waiver_id)
    except SourceError as error:
        _raise_from(error)
    audit(user, "waiver_revoked", {"student_id": student_id, "waiver_id": waiver_id})
    return {"revoked": True}


# ------------------------------------------------------------------ knowledge queries
def answer_query(data: QueryRequest, user: dict):
    tenant = user["tenant_id"]
    if data.student_id:
        student_access(user, data.student_id)
    generation = source().manifest(tenant)["generation"]
    key = runtime.cache_key(tenant, generation, data.student_id or "", data.question)
    result = runtime.cache_get(key)
    if result is None:
        result = workflow.invoke({"user": user, "question": data.question, "student_id": data.student_id})["result"]
        runtime.cache_put(key, result, ttl_seconds=300)
    # Recheck grants and cited records before releasing any evidence.
    if data.student_id:
        student_access(user, data.student_id)
    for item in result["evidence"]:
        if not source().evidence(tenant, item["id"]):
            raise HTTPException(404, "Evidence not found")
    request_id = str(uuid4())
    audit(user, "query", {"request_id": request_id, "evidence_count": len(result["evidence"]),
                          "manifest": result["manifest"], "generator": result["generator"]})
    return {**result, "request_id": request_id}


@app.post("/api/v1/kag/query", response_model=QueryResponse)
def query(data: QueryRequest, user=Depends(current_user)):
    if not data.stream:
        return answer_query(data, user)
    if data.student_id:
        student_access(user, data.student_id)

    def events():
        yield 'event: status\ndata: {"message":"Finding evidence and evaluating prerequisites…"}\n\n'
        try:
            result = answer_query(data, user)
            yield f"event: result\ndata: {json.dumps(result)}\n\n"
        except Exception:
            log.exception("Query stream failed without logging content")
            yield 'event: error\ndata: {"message":"The answer could not be completed. Please try again."}\n\n'
        yield 'event: done\ndata: {}\n\n'
    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.post("/api/v1/kag/recommend", response_model=QueryResponse)
def recommend(data: RecommendationRequest, user=Depends(current_user)):
    return answer_query(QueryRequest(question=data.goal, student_id=data.student_id), user)


@app.post("/api/v1/feedback", status_code=201)
def feedback(data: FeedbackRequest, user=Depends(current_user)):
    response = source().post_raw("/internal/feedback", {"tenant": user["tenant_id"], "user_id": user["id"],
                                                         "request_id": data.request_id, "rating": data.rating, "comment": data.comment})
    if response.status_code == 404:
        raise HTTPException(404, "Query not found")
    if response.status_code >= 400:
        raise HTTPException(502, "Feedback could not be saved")
    return {"saved": True}


# ------------------------------------------------------------------ administration
@app.post("/api/v1/admin/ingestion", status_code=202)
def ingestion(data: IngestRequest, user=Depends(admin)):
    payload = {"title": data.title, "text": data.text, "skill_id": data.skill_id}
    try:
        status, job = source().create_job(user["tenant_id"], user["id"], data.idempotency_key, payload)
    except SourceError as error:
        _raise_from(error)
    if job.get("created"):
        submit_ingest(user["tenant_id"], job["id"])
    return {"id": job["id"], "status": job["status"]}


@app.get("/api/v1/admin/ingestion")
def jobs(user=Depends(admin)):
    return {"items": source().jobs(user["tenant_id"])}


@app.get("/api/v1/admin/ingestion/{job_id}")
def job(job_id: str, user=Depends(admin)):
    row = source().job(user["tenant_id"], job_id)
    if not row:
        raise HTTPException(404, "Job not found")
    return row


@app.get("/api/v1/admin/audit")
def audits(user=Depends(admin)):
    return {"items": source().audits(user["tenant_id"])}
