# Proposed implementation structure

The files below are a target structure, not existing application modules. `contracts.py` alongside this document is a standalone example. It requires Pydantic 2 and is not imported by the application; the generation adapter lives in `backend/app/kag.py`.

```text
apps/web/                        Next.js, TypeScript, Tailwind, shadcn/ui
  app/{student,teacher,advisor,admin}/
  components/{chat,evidence,learning-path,progress,graph}/
  lib/{session,api-client,stream-parser}/
services/api/
  app/main.py                   FastAPI app, lifespan, readiness, middleware
  app/api/v1/{kag,curriculum,students,knowledge,ingestion,feedback,evidence}.py
  app/api/{dependencies,errors,streaming}.py
  app/core/{config,authentication,authorization,telemetry,limits}.py
  app/contracts/{queries,responses,evidence,scope,manifest}.py
  app/workflows/{query_graph,state,nodes}.py
  app/domain/{facts,requirements,learning_path,mastery,grounding}.py
  app/ports/{source,vector,graph,policy,generator,evidence,manifest}.py
  app/adapters/{source_http,qdrant,neo4j,opa,bedrock,embeddings}.py
  app/repositories/cypher/       Fixed parameterized traversal templates
services/source/
  app/main.py                   Internal authenticated FastAPI source API
  app/auth/                     Workload identity + validated delegated scope
  app/db/{models,session,repositories,migrations}/
  app/api/{records,grants,snapshots,outbox,manifests,jobs,tombstones}.py
  app/domain/{leases,publication,versioning}.py
  app/maintenance/{backup,integrity}.py
services/worker/
  app/celery_app.py
  app/tasks/{dispatch,extract,normalize,resolve,graph,embed,vector,validate,publish,delete}.py
  app/pipeline/{idempotency,retries,reconciliation,quarantine}.py
  app/parsers/{pdf,unstructured,ocr}.py
  app/schedules.py              One scheduler instance only
packages/domain/                Shared types and deterministic rules
policies/                       Versioned OPA bundles + tests
fixtures/                       Synthetic students; imported licensed curriculum
tests/{unit,integration,security,e2e,evaluation,recovery}/
infra/modules/                  OpenTofu modules (proposed)
infra/environments/{dev,staging,prod}/
```

## Service boundaries

- The source process alone mounts and opens `education.db`. SQLAlchemy is internal to that service. API/workers call authenticated source endpoints over the network; they do not mount SQLite.
- The source service commits educational writes and outbox records atomically. It leases jobs and publishes manifests via compare-and-swap. Ingestion writes to graph/vector storage, then requests publication through the source API.
- `AuthorizedScope` is constructed from verified identity and policy. A deserialized object is not itself proof of authorization. Internal calls must verify issuer/audience/signature, caller workload, delegation and current policy before creating scope.
- A source snapshot adapter returns curriculum versions matching the manifest. Current grants, tombstones and student evidence are checked separately and their timestamps recorded.
- Public graph endpoints accept approved entity filters, never arbitrary Cypher. LLM output never becomes SQL, Cypher or Python code.
- The generation adapter receives minimized, validated evidence; the orchestration layer performs citation/claim validation and permission rechecks before returning it.

## Essential tests to implement

Authorization must test another tenant's IDs, stale grants, unauthorized path intermediates, cache collisions and citation dereferences. Rule fixtures must cover missing evidence, effective dates, cycles, AND/OR groups and waivers. Ingestion tests must prove duplicate convergence, stale-event rejection, all-or-nothing publication and deletion propagation. Integration tests need actual Qdrant/Neo4j containers; mocks alone cannot establish query filtering or index consistency.
