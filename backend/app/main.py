import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .db import db, initialize
from .ingestion import publish, rebuild_indexes
from .kag import workflow
from .reasoning import knowledge_graph, learning_path
from .security import admin, current_user, enforce_local_demo, new_session, student_access, verify_password
from .seed import seed

log = logging.getLogger("kag")


def now():
    return datetime.now(timezone.utc).isoformat()


def audit(user, action, metadata=None):
    with db() as connection:
        connection.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?)", (str(uuid4()),user["tenant_id"],user["id"],action,json.dumps(metadata or {}),now()))


@asynccontextmanager
async def lifespan(_):
    enforce_local_demo()
    initialize()
    seed()
    # Derived indexes (Qdrant/Neo4j) are rebuilt from published SQLite rows; a no-op offline.
    rebuild_indexes()
    # Durable job ledger makes jobs interrupted by a process exit visible.
    with db() as connection:
        connection.execute("UPDATE ingestion_jobs SET status='failed', error='Process restarted before completion; submit a new job.' WHERE status='queued'")
    yield


app = FastAPI(title="Northstar Educational KAG", version="1.0.0", lifespan=lifespan)
attempts = defaultdict(deque)
rate_lock = threading.Lock()
origins = set(os.getenv("KAG_ALLOWED_ORIGINS","http://localhost:3000,http://127.0.0.1:3000").split(","))


@app.middleware("http")
async def request_controls(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method in {"POST","PATCH","PUT","DELETE"} and origin and origin not in origins:
        return JSONResponse({"detail":"Untrusted request origin"},status_code=403)
    ip = request.client.host if request.client else "unknown"
    bucket = (ip, "login" if request.url.path.endswith("/login") else "api")
    limit = 30 if bucket[1] == "login" else 180
    clock = time.monotonic()
    with rate_lock:
        history = attempts[bucket]
        while history and history[0] < clock - 60:
            history.popleft()
        if len(history) >= limit:
            return JSONResponse({"detail":"Too many requests. Please try again shortly."},status_code=429,headers={"Retry-After":"60"})
        history.append(clock)
    request_id = str(uuid4())
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f'app;dur={(time.monotonic()-started)*1000:.1f}'
    return response


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    email: str = Field(min_length=3,max_length=200)
    password: str = Field(min_length=1,max_length=200)


@app.post("/api/v1/auth/login")
def login(data: Login, response: Response):
    with db() as connection:
        row = connection.execute("SELECT * FROM users WHERE email=?",(data.email.lower(),)).fetchone()
    if not row or not verify_password(data.password,row["password_hash"]):
        raise HTTPException(401,"Email or password is incorrect")
    token = new_session(row["id"])
    response.set_cookie("kag_session",token,max_age=28800,httponly=True,secure=os.getenv("KAG_COOKIE_SECURE","false") == "true",samesite="lax",path="/api")
    user = {k:row[k] for k in ["id","tenant_id","name","email","role"]}
    audit(user,"login")
    return user


@app.post("/api/v1/auth/logout")
def logout(request: Request,response: Response,user=Depends(current_user)):
    with db() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(request.cookies.get("kag_session","").encode()).hexdigest(),))
    response.delete_cookie("kag_session",path="/api")
    return {"ok":True}


@app.get("/api/v1/auth/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/v1/health")
def health():
    return {"status":"ok","application":"Northstar KAG","demo":True}


@app.get("/api/v1/overview")
def overview(user=Depends(current_user)):
    tenant = user["tenant_id"]
    with db() as connection:
        students = [dict(r) for r in connection.execute("SELECT s.id,s.name,s.grade FROM students s JOIN access_grants g ON s.id=g.student_id WHERE s.tenant_id=? AND g.user_id=? ORDER BY s.name",(tenant,user["id"]))]
        resources = connection.execute("SELECT COUNT(*) FROM resources WHERE tenant_id=?",(tenant,)).fetchone()[0]
        standards = connection.execute("SELECT COUNT(*) FROM curriculum_standards WHERE tenant_id=?",(tenant,)).fetchone()[0]
        skills = connection.execute("SELECT COUNT(*) FROM skills WHERE tenant_id=?",(tenant,)).fetchone()[0]
        queries = connection.execute("SELECT COUNT(*) FROM audit_events WHERE tenant_id=? AND action='query' AND user_id=?",(tenant,user["id"])).fetchone()[0]
        generation = connection.execute("SELECT generation,updated_at FROM index_manifests WHERE tenant_id=?",(tenant,)).fetchone()
    return {"user":user,"students":students,"stats":{"resources":resources,"standards":standards,"skills":skills,"queries":queries},"manifest":dict(generation) if generation else None,"bedrock_configured":bool(os.getenv("BEDROCK_MODEL_ID")),"bedrock_model_id":os.getenv("BEDROCK_MODEL_ID") or None,"bedrock_region":os.getenv("AWS_REGION","us-east-1"),"retrieval":"Local BM25 + hashed vectors","graph":"SQLite + NetworkX","demo":True}


@app.get("/api/v1/curriculum/standards")
def standards(q: str = "", grade: int | None = None, state: str | None = None,user=Depends(current_user)):
    with db() as connection:
        rows = [dict(r) for r in connection.execute("SELECT * FROM curriculum_standards WHERE tenant_id=? ORDER BY id",(user["tenant_id"],))]
    return {"items":[r for r in rows if (not q or q.lower() in (r["title"]+r["description"]).lower()) and (grade is None or r["grade"]==grade) and (not state or r["state"]==state)],"notice":"DEMO identifiers are illustrative; they are not official state-standard codes."}


@app.get("/api/v1/resources")
def resources(q: str = "", user=Depends(current_user)):
    with db() as connection:
        rows = [dict(r) for r in connection.execute("SELECT id,title,text,skill_id,kind,source_uri,source_version,created_at FROM resources WHERE tenant_id=? AND auth_scope='curriculum' ORDER BY created_at DESC,title",(user["tenant_id"],))]
    return {"items":[r for r in rows if not q or q.lower() in (r["title"]+r["text"]).lower()]}


@app.get("/api/v1/evidence/{evidence_id}")
def evidence(evidence_id: str,user=Depends(current_user)):
    with db() as connection:
        row = connection.execute("SELECT id,title,text,source_version,source_uri FROM resources WHERE id=? AND tenant_id=? AND auth_scope='curriculum'",(evidence_id,user["tenant_id"])).fetchone()
    if not row:
        raise HTTPException(404,"Evidence not found")
    return dict(row)


@app.get("/api/v1/students/{student_id}/learning-path")
def path(student_id: str,user=Depends(current_user)):
    return learning_path(user,student_id)


@app.get("/api/v1/knowledge/graph")
def graph(user=Depends(current_user)):
    return knowledge_graph(user)


class Query(Strict):
    question: str = Field(min_length=3,max_length=4000)
    student_id: str | None = Field(default=None,max_length=100)
    stream: bool = False


def answer_query(data: Query,user: dict):
    if data.student_id:
        student_access(user,data.student_id)
    result = workflow.invoke({"user":user,"question":data.question,"student_id":data.student_id})["result"]
    # Recheck grants and referenced sources before releasing potentially personal evidence.
    if data.student_id:
        student_access(user,data.student_id)
    for item in result["evidence"]:
        evidence(item["id"],user)
    request_id = str(uuid4())
    audit(user,"query",{"request_id":request_id,"evidence_count":len(result["evidence"]),"manifest":result["manifest"],"generator":result["generator"]})
    return {**result,"request_id":request_id}


@app.post("/api/v1/kag/query")
def query(data: Query,user=Depends(current_user)):
    if not data.stream:
        return answer_query(data,user)
    # Authenticate/authorize before HTTP stream headers are committed.
    if data.student_id:
        student_access(user,data.student_id)

    def events():
        yield 'event: status\ndata: {"message":"Finding evidence and evaluating prerequisites…"}\n\n'
        try:
            result = answer_query(data,user)
            yield f"event: result\ndata: {json.dumps(result)}\n\n"
        except Exception:
            log.exception("Query stream failed without logging content")
            yield 'event: error\ndata: {"message":"The answer could not be completed. Please try again."}\n\n'
        yield 'event: done\ndata: {}\n\n'
    return StreamingResponse(events(),media_type="text/event-stream",headers={"X-Accel-Buffering":"no"})


class Recommendation(Strict):
    goal: str = Field(default="Prepare for Algebra I",min_length=3,max_length=4000)
    student_id: str | None = Field(default=None,max_length=100)


@app.post("/api/v1/kag/recommend")
def recommend(data: Recommendation,user=Depends(current_user)):
    return answer_query(Query(question=data.goal,student_id=data.student_id),user)


class Feedback(Strict):
    request_id: str = Field(max_length=100)
    rating: int = Field(ge=1,le=5)
    comment: str = Field(default="",max_length=2000)


@app.post("/api/v1/feedback",status_code=201)
def feedback(data: Feedback,user=Depends(current_user)):
    # Request ownership is verified against redacted query audit metadata.
    with db() as connection:
        exists = connection.execute("SELECT 1 FROM audit_events WHERE tenant_id=? AND user_id=? AND action='query' AND json_extract(metadata,'$.request_id')=?",(user["tenant_id"],user["id"],data.request_id)).fetchone()
        if not exists:
            raise HTTPException(404,"Query not found")
        connection.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?,?)",(str(uuid4()),user["tenant_id"],user["id"],data.request_id,data.rating,data.comment,now()))
    return {"saved":True}


class Ingest(Strict):
    title: str = Field(min_length=3,max_length=180)
    text: str = Field(min_length=30,max_length=50000)
    skill_id: str | None = Field(default=None,max_length=100)
    idempotency_key: str = Field(min_length=8,max_length=100)


def ingest_job(job_id: str,data: Ingest,user: dict):
    try:
        with db() as connection:
            job = connection.execute("SELECT status FROM ingestion_jobs WHERE id=? AND tenant_id=?",(job_id,user["tenant_id"])).fetchone()
            if not job or job["status"] != "queued":
                return
        # Index and publish atomically. Identical content converges to one resource.
        published = publish(user["tenant_id"],[{"title":data.title,"text":data.text,"skill_id":data.skill_id}])
        with db() as connection:
            connection.execute("UPDATE ingestion_jobs SET status='completed',resource_id=? WHERE id=?",(published["resource_ids"][0],job_id))
        audit(user,"ingestion_completed",{"job_id":job_id,"published_generation":published["generation"],"changed":published["changed"]})
    except Exception:
        log.exception("Ingestion job failed; no partial generation was published")
        with db() as connection:
            connection.execute("UPDATE ingestion_jobs SET status='failed',error='Import failed validation or storage; retry with a new key.' WHERE id=?",(job_id,))


@app.post("/api/v1/admin/ingestion",status_code=202)
def ingestion(data: Ingest,tasks: BackgroundTasks,user=Depends(admin)):
    payload_hash = hashlib.sha256(json.dumps(data.model_dump(exclude={"idempotency_key"}),sort_keys=True).encode()).hexdigest()
    with db() as connection:
        existing = connection.execute("SELECT * FROM ingestion_jobs WHERE tenant_id=? AND idempotency_key=?",(user["tenant_id"],data.idempotency_key)).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(409,"Idempotency key already used with different content")
            return dict(existing)
        if data.skill_id and not connection.execute("SELECT 1 FROM skills WHERE id=? AND tenant_id=?",(data.skill_id,user["tenant_id"])).fetchone():
            raise HTTPException(422,"Unknown skill")
        job_id = str(uuid4())
        connection.execute("INSERT INTO ingestion_jobs VALUES (?,?,?,?,?,?,?,?,?)",(job_id,user["tenant_id"],user["id"],data.idempotency_key,payload_hash,"queued",None,None,now()))
    tasks.add_task(ingest_job,job_id,data,user)
    return {"id":job_id,"status":"queued"}


@app.get("/api/v1/admin/ingestion")
def jobs(user=Depends(admin)):
    with db() as connection:
        return {"items":[dict(r) for r in connection.execute("SELECT id,status,resource_id,error,created_at FROM ingestion_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT 50",(user["tenant_id"],))]}


@app.get("/api/v1/admin/ingestion/{job_id}")
def job(job_id: str,user=Depends(admin)):
    with db() as connection:
        row = connection.execute("SELECT id,status,resource_id,error,created_at FROM ingestion_jobs WHERE tenant_id=? AND id=?",(user["tenant_id"],job_id)).fetchone()
    if not row:
        raise HTTPException(404,"Job not found")
    return dict(row)


@app.get("/api/v1/admin/audit")
def audits(user=Depends(admin)):
    with db() as connection:
        return {"items":[{**dict(r),"metadata":json.loads(r["metadata"])} for r in connection.execute("SELECT id,user_id,action,metadata,created_at FROM audit_events WHERE tenant_id=? ORDER BY created_at DESC LIMIT 50",(user["tenant_id"],))]}
