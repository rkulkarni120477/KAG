# Northstar — Educational KAG application

Working local pilot: Next.js / React, FastAPI, SQLite, LangGraph, hybrid retrieval, NetworkX learning-path reasoning, and optional Amazon Bedrock generation.

**Web:** http://localhost:3000 · **API documentation:** http://localhost:8000/docs

Choose a Student, Teacher, Advisor, or Admin account on the sign-in screen. Default demo password: **`demo-pass-2026`**.

## Run locally

Requires Python 3.11+ and Node.js 20.9+.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

This creates `.venv`, installs dependencies, starts both processes, and checks readiness. Logs are in `.runtime/`. Use `-SkipInstall` on subsequent launches when dependencies are installed. Stop with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop-local.ps1
```

Manual launch in two terminals:

```powershell
# Terminal 1: repository root
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
# Terminal 2
cd frontend
npm run dev
```

## Run with Docker

```powershell
docker compose up --build -d
docker compose ps
```

The root [compose.yaml](compose.yaml) builds both application images and persists SQLite in the `education_data` volume. Ports bind to loopback. One API worker owns the database. Stop with `docker compose down`; preserve the volume for persistent data. The older `deployment/compose.yaml` is an architecture reference, not this application's launcher.

## Working features

- Responsive student/faculty/advisor/admin portals with session login and sign-out.
- Expiring HttpOnly session cookies, PBKDF2 password hashing, throttling, origin checks, tenant filtering, and student-object access controls.
- Curriculum search, source inspection, illustrative standards, interactive knowledge graph and accessible relationship table.
- LangGraph retrieval/reasoning/generation workflow with local BM25 + deterministic hashed-vector ranking and SSE results.
- Deterministic prerequisite ordering and three-valued mastery: demonstrated, below the demo threshold, or unknown.
- Admin text ingestion, durable job status, idempotency checks, transactional resource publication and generation updates.
- Persistent feedback tied to owned queries and redacted audit metadata.
- Optional Bedrock explanations; evidence-only fallback when AWS is absent or a response fails validation.

## Data and authorization

All fixtures are synthetic. `DEMO-READINESS-*` identifiers are **not official state-standard codes**. The 80% mastery threshold is illustrative. Answers are advisory, and no enrollment, grading, placement or eligibility actions occur.

Student sees Alex; Teacher sees Alex and Jamie; Advisor sees Alex. Admin manages curriculum and audit metadata but has no student-record grants. A separate inaccessible tenant is seeded for isolation tests. Tenant IDs are derived from the authenticated session.

API account emails are `student@northstar.demo`, `teacher@northstar.demo`, `advisor@northstar.demo`, and `admin@northstar.demo`. SQLite is in `backend/data/education.db`; set `KAG_DATA_DIR` to change it. Accounts seed only once. Changing `KAG_DEMO_PASSWORD` affects only initial seeding, not existing password hashes.

## Optional Bedrock

Set `AWS_REGION` and an account-enabled `BEDROCK_MODEL_ID` in `backend/.env` or the backend process environment before launch. The backend automatically loads `backend/.env` without overriding process variables. Compose passes optional root `.env` and `backend/.env` files to the API container (the backend file takes precedence). Recreate the API container after editing an env file; a plain restart does not reload Compose environment settings.

```powershell
$env:AWS_REGION = 'your-approved-region'
$env:BEDROCK_MODEL_ID = 'your-enabled-model-id'
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -SkipInstall
```

For a Bedrock API key, set `AWS_BEARER_TOKEN_BEDROCK` in `backend/.env`; the installed Boto3 version supports it. Alternatively configure short-lived credentials through the SDK credential chain or a workload role. No keys belong in frontend code. Env files are Git-ignored and excluded from the Docker build context; credentials are injected at runtime. Compose intentionally does not mount your home credential directory. Model inference can incur charges. AI explanations are separately labeled; shape/citation-ID checks are **not a full semantic grounding guarantee**. Deterministic source evidence and rule outcomes remain separate.

Configured model (10 November 2026): `us.anthropic.claude-fable-5-2`, with the US geographic inference profile and `AWS_REGION=us-east-1`. This can route inference across US AWS regions; it is not a single-region deployment guarantee. [AWS model details](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5-2.html). Fable uses low reasoning effort for this application; `BEDROCK_MAX_OUTPUT_TOKENS` defaults to 2048 and is capped at 4096. Keep model changes explicit rather than silently following a changing latest alias.

After changing `backend/.env`:

```powershell
docker compose up -d --build api
```

## Validation

```powershell
# From backend/
../.venv/Scripts/python.exe -m pytest tests -q
# From frontend/
npm run build
```

Integration tests use isolated SQLite and real API/session/orchestration code to check tenant/student isolation, admin boundaries, mastery semantics, prerequisite ordering, SSE, feedback ownership, source resolution, idempotency and retrieval after import.

## Deployment boundaries

This is a runnable **local pilot**, not the entire enterprise architecture. Runtime uses local sessions rather than Keycloak/OPA, BM25/hashed vectors rather than Qdrant/Sentence Transformers, and SQLite/NetworkX rather than a Neo4j server. Ingestion uses FastAPI background tasks and a durable SQLite job ledger rather than Celery/Redis; interrupted jobs are marked failed after restart and can be resubmitted with a new key. Imports accept plain text, not PDF/OCR. External services are not silently simulated.

Startup refuses `KAG_DEMO_MODE=false`. Production SSO, external indexes, TLS, governance, retention/deletion workflows, distributed workers, monitoring, backups, restoration and AWS IaC remain production work before real student data. The local app uses HTTP on loopback; enterprise HTTPS remains a deployment gate. Docker volumes alone are not encryption or backup.

## Original architecture reference

[Download the historical design-only package](educational-kag-architecture.zip). This archive excludes the application source added afterward.

- [Detailed architecture and all ten deliverables](docs/architecture.md)
- [Logical architecture — 4K PNG](diagrams/logical-architecture.png) · [editable SVG](diagrams/logical-architecture.svg)
- [AWS deployment — 4K PNG](diagrams/aws-deployment.png) · [editable SVG](diagrams/aws-deployment.svg)
- [Query sequence — 4K PNG](diagrams/query-sequence.png) · [editable SVG](diagrams/query-sequence.svg)
- [Knowledge graph model — 4K PNG](diagrams/knowledge-graph.png) · [editable SVG](diagrams/knowledge-graph.svg)
- [Docker Compose blueprint](deployment/compose.yaml) · [setup and limitations](deployment/README.md)
- [FastAPI implementation structure](implementation/STRUCTURE.md) · [typed contracts](implementation/contracts.py) · [Bedrock adapter example](implementation/bedrock_adapter.py)
- [Diagram source and regeneration](tools/render_diagrams.py)

The documents above describe the wider target architecture. Runtime entry points are [backend/app/main.py](backend/app/main.py), [backend/app/kag.py](backend/app/kag.py), [frontend/app/page.tsx](frontend/app/page.tsx), and the root [compose.yaml](compose.yaml). No AWS infrastructure has been provisioned.
