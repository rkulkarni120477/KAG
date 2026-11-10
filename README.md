# Northstar — Educational KAG application

A synthetic-data pilot of a knowledge-augmented guidance platform for education. It has student, teacher, advisor and administrator portals; a prerequisite rule model with ALL/ANY groups, effective dates and waivers; hybrid retrieval over chunked curriculum; and optional Amazon Bedrock generation with evidence-only fallback.

Services: Next.js web app · FastAPI API · source service (the only process that owns SQLite) · worker and scheduler (RQ on Redis) · Qdrant · Neo4j · Keycloak (OIDC) · OPA (policy) · Caddy gateway.

## Run the full stack (Docker)

Requirements: Docker Compose v2 on Linux containers. Set up the environment file once:

```powershell
Copy-Item deployment/.env.example deployment/.env
# Replace each REPLACE_WITH_... value with a random secret and each image digest with a reviewed digest.
docker compose --env-file deployment/.env -f deployment/compose.yaml --profile app up -d --build
```

Open **https://localhost:8443** (the gateway uses a local certificate authority; accept or trust it). Stop with `docker compose -f deployment/compose.yaml --profile app down`. Details, secrets handling and limits are in [deployment/README.md](deployment/README.md).

Demo accounts (password `demo-pass-2026`): `student@northstar.demo`, `teacher@northstar.demo`, `advisor@northstar.demo`, `admin@northstar.demo`. Accounts and passwords are defined in [deployment/keycloak/education-realm.json](deployment/keycloak/education-realm.json) and are for synthetic data only.

## Run locally without Docker

Requires Python 3.11+, Node.js 20.9+, and a Keycloak with the education realm on `localhost:8080` (the realm file above; start it with `start-dev --import-realm`).

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
powershell -ExecutionPolicy Bypass -File scripts/stop-local.ps1
```

The script starts the source service (8001), the API (8000) and the web app (3000), using inline jobs and offline indexes. Logs are in `.runtime/`.

## Tests

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests -q                       # offline: SQLite, hashed embeddings, in-memory Redis
```

Live integration tests run when their services are configured (they are skipped otherwise):

- `KAG_VECTOR_BACKEND=qdrant QDRANT_URL=http://localhost:6333` and `KAG_GRAPH_BACKEND=neo4j NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=...` run the same suite against Qdrant and Neo4j.
- `OPA_TEST_URL=http://localhost:8181` checks the Rego policy against the in-process rule.
- `KEYCLOAK_TOKEN_URL=... OIDC_JWKS_URL=...` validates tokens issued by the realm.

Frontend: `npm run build` in `frontend/`.

## Features

- **Portals.** Student workspace (path, assistant, curriculum, graph); teacher portal (roster, prerequisite path with rule groups, waivers); advisor portal (caseload, skill gaps, cited advising briefs); administrator (ingestion, audit).
- **Identity.** Keycloak-issued RS256 tokens validated for signature, issuer, audience and expiry; each token's tenant, user and role are cross-checked against the source service.
- **Authorization.** A student record needs a current grant, the same tenant, and a purpose the caller's role may use: teachers for instruction, advisors for advising, students for their own self-service view. Administrators hold no record purpose. OPA evaluates the same rule when configured; both fail closed.
- **Rules.** Prerequisites are AND/OR groups (`ALL` / `ANY`), effective-dated, and can be waived per student with a documented reason, an effective window and revocation. Cycles are rejected across every rule, including future-dated ones. Missing evidence is "unknown", not failure. The mastery threshold comes from the academic policy.
- **Ingestion.** Queued through Redis to the worker. Validation, content-addressed identity (duplicate content converges), entity linking, sentence-based chunking, embedding, indexing, then an atomic commit with a generation compare-and-swap. Failed jobs publish nothing.
- **Retrieval.** BM25 over published resources plus chunk-level vector ranking (Qdrant, or offline hashed vectors). Lexical match is always required, so vector collisions alone cannot establish relevance.
- **Redis.** Job queue, API rate limiting, login-failure lockout, and a query-answer cache keyed by tenant, published generation and question.
- **Maintenance.** The scheduler purges expired web-cache results every few hours, removing them from SQLite and then from Qdrant and Neo4j.
- **Generation.** Optional Bedrock explanations (Converse API) validated against an output schema and cited evidence IDs; evidence-only answers otherwise.

## Limits

This is a **synthetic-data pilot**. Not production-ready:

- Login uses the Keycloak password grant so the existing form works; production uses Authorization Code with PKCE through the gateway.
- Access tokens are not revocable; logout removes the cookie only. Keep token lifetime short.
- The source service authenticates callers with a shared service token; production needs workload identity (mTLS or signed service tokens).
- Embeddings: the offline mode uses hashed vectors, which are not a trained model. Qdrant mode uses Amazon Titan Text Embeddings v2 when `BEDROCK_EMBED_MODEL_ID` is set; that path is covered by mocked tests only, not a live model call.
- Single source-service process owning SQLite. Not for multi-host deployment.
- Rules are seeded with the demo curriculum; there is no rule-authoring screen.
- Imports accept plain text, not PDF or OCR.
- No AWS infrastructure is provisioned. Enterprise TLS, backups, monitoring and retention workflows are deployment work.

## Data and authorization

All fixtures are synthetic. `DEMO-READINESS-*` identifiers are **not official state-standard codes**. The 80% mastery threshold is illustrative. Answers are advisory; no enrollment, grading, placement or eligibility decision is made.

Student sees Alex; teacher sees Alex and Jamie; advisor sees Alex. A separate tenant is seeded for isolation tests.

## Documentation

- [Architecture and deliverables](docs/architecture.md)
- [Logical architecture](diagrams/logical-architecture.svg) · [AWS deployment](diagrams/aws-deployment.svg) · [Query sequence](diagrams/query-sequence.svg) · [Knowledge graph model](diagrams/knowledge-graph.svg)
- [Deployment and limits](deployment/README.md) · [Compose stack](deployment/compose.yaml)
- [Module structure](implementation/STRUCTURE.md) · [Typed contracts](backend/app/contracts.py)
- [Historical design-only package](educational-kag-architecture.zip)
