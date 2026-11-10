# Implementation structure

This describes the modules that exist in the repository and the boundaries between them.

```text
frontend/                          Next.js app: student workspace, /teacher and /advisor portals
  app/page.tsx                     student workspace (login, path, assistant, curriculum, graph, admin)
  app/teacher/page.tsx             roster, prerequisite path, waivers
  app/advisor/page.tsx             caseload, skill gaps, cited advising briefs
  components/portal/shell.tsx      shared portal shell, role guard, status components
  lib/api.ts                       typed API client

backend/app/
  main.py                          API routes, origin checks, rate limits, login lockout, query cache
  auth.py                          Keycloak token validation (JWKS) and password grant
  security.py                      current user (token + source cross-check), student-record access
  policy.py                        purpose-based decisions; OPA when configured, else the same rule
  contracts.py                     request, response and AI-output schemas
  reasoning.py                     prerequisite rules: ALL/ANY groups, effective dates, waivers, thresholds
  retrieval.py                     BM25 + chunk-vector fusion; lexical match always required
  kag.py                           LangGraph workflow: retrieve, reason, generate, validate
  indexes.py                       Qdrant and Neo4j adapters (SQLite fallbacks offline)
  embeddings.py                    Bedrock Titan v2 embedder; hashed offline embedder
  runtime.py                       Redis: RQ queue, rate limits, failure counters, query cache
  source_client.py                 HTTP client for the source service
  web_search.py                    optional online fallback (Wikipedia, SearXNG)
  domain/documents.py              validation, content identity, entity linking, chunking (pure)
  source/                          the source service (owns SQLite)
    db.py                          schema and connection; only this process opens the file
    repository.py                  all SQL; every query is tenant-scoped
    seed.py                        synthetic demo tenants, curriculum and rules
    main.py                        internal API; requires the service token except liveness
  worker/
    pipeline.py                    prepare -> chunk -> embed -> index -> commit; purge; rebuild
    tasks.py                       queued jobs (ingestion, web publication, purge)
    __main__.py                    `run` (RQ consumer) and `scheduler` (purge interval)

backend/Dockerfile                 API image
backend/Dockerfile.source          source service image
backend/Dockerfile.worker          worker and scheduler image

deployment/compose.yaml            full stack; see deployment/README.md
deployment/keycloak/               education realm (users, clients, audience and claim mappers)
deployment/policies/education.rego policy: grant, tenant and purpose
deployment/Caddyfile               TLS gateway routes
```

## Boundaries

- **Only the source service opens `education.db`.** The API and worker call it over HTTP with the service token. The source service also owns the atomic parts of publication and job state.
- **Identity comes from Keycloak; authorization comes from the source and the policy.** A token proves who the caller is. The source confirms the user's tenant and role, and the policy decides whether that role may act on a specific student for a specific purpose.
- **Indexes are derived.** Qdrant and Neo4j are rebuilt from published rows by the worker at startup. Reads intersect index hits with published rows.
- **Model output is never a query or a fact.** It is validated against `AIDraft` and cited evidence IDs before it reaches the user. The answer text is drawn from the evidence.
- **Publication is two-phase.** The worker writes index points at the next generation; the source commits the generation compare-and-swap and the rows in one transaction. A stale or conflicting publication is rejected and retried with a fresh generation.

## Tests

`backend/tests` runs offline by default. The same suite runs against live Qdrant, Neo4j, OPA and Keycloak when their environment variables are set (see the README). Index, policy and realm behaviour are tested against real services, not mocks alone.
