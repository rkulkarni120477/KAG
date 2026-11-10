# Deployment: Docker Compose stack

`compose.yaml` defines the full local stack. Dependency services start by default; the `app` profile adds the application services.

| Service | Role | Port (host) |
|---|---|---|
| gateway (Caddy) | TLS entry point; routes `/api`, `/auth` and the web app | 127.0.0.1:8443 |
| web (Next.js) | Student, teacher and advisor portals | internal |
| api (FastAPI) | Stateless HTTP API; validates tokens; enforces policy | internal |
| source (FastAPI) | The only process that owns the SQLite database | internal |
| worker (RQ) | Ingestion, chunking, embedding, indexing, purge | internal |
| scheduler | Enqueues web-cache purges on an interval | internal |
| redis | Job queue, rate limiting, lockout counters, query cache | internal |
| keycloak | OIDC identity; imports `keycloak/education-realm.json` | internal |
| opa | Policy decisions from `policies/education.rego` | internal |
| qdrant, neo4j | Derived vector and graph indexes | internal |

Only the gateway publishes a host port. Services share the `knowledge` network, which has no outbound route; `egress` is attached only where a service needs it.

## Setup

1. Create the environment file and replace every placeholder:

   ```powershell
   Copy-Item deployment/.env.example deployment/.env
   ```

   - Image values use `repository@sha256:<digest>`. Use reviewed digests. The stack was verified with Qdrant 1.20.0, Neo4j 5.26 Community, Redis 8, Keycloak 26.4, OPA latest-static, and Caddy 2.
   - Secrets (`SOURCE_SERVICE_TOKEN`, `QDRANT_API_KEY`, `NEO4J_PASSWORD`, `REDIS_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`) must be random. `.env` is gitignored.
   - `BEDROCK_MODEL_ID` and `BEDROCK_EMBED_MODEL_ID` are optional. Without them the answers are evidence-only and embeddings are the labeled hashed vectors.

2. Validate the configuration without printing secrets:

   ```powershell
   docker compose --env-file deployment/.env -f deployment/compose.yaml --profile app config --quiet
   ```

3. Start the stack:

   ```powershell
   docker compose --env-file deployment/.env -f deployment/compose.yaml --profile app up -d --build
   ```

   Then open https://localhost:8443. The gateway's certificate is issued by Caddy's local authority; trust it in the browser or accept the warning for local use.

Stop with `docker compose -f deployment/compose.yaml --profile app down`. Add `-v` only if you intend to delete the data volumes.

## Keycloak only

To run the realm without the rest of the stack (for the local launcher or tests):

```powershell
docker run -d --name kag-keycloak -p 8080:8080 `
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=<local-secret> `
  -e KC_HTTP_RELATIVE_PATH=/auth -e KC_HOSTNAME=https://localhost:8443/auth -e KC_PROXY_HEADERS=xforwarded `
  -v "${PWD}/deployment/keycloak:/opt/keycloak/data/import:ro" `
  quay.io/keycloak/keycloak:26.4 start-dev --import-realm
```

Tokens carry the public issuer `https://localhost:8443/auth/realms/education`, which the API validates even when it fetches keys from the internal address.

## How authentication works

- `/api/v1/auth/login` exchanges the demo credentials with Keycloak (password grant) and stores the access token in an HttpOnly cookie. The cookie's lifetime matches the token's expiry.
- Every request validates the token: RS256 signature from the realm JWKS, issuer, audience `kag-api`, expiry, and required claims (`tenant_id`, `kag_user_id`, `role`).
- The API then checks that the user exists in the source service with the same tenant and role. A token that is valid but disagrees with the source is refused.
- Repeated failed logins for one account are locked for 15 minutes (counters in Redis).

## How data moves

1. Admin submits ingestion. The API validates the request, asks the source service to create an idempotent job, and enqueues it on Redis.
2. The worker runs the pipeline: the source validates and content-addresses the document; the worker chunks, embeds and indexes it at the next generation; the source commits the generation and rows in one transaction.
3. A failed step leaves no published rows. Index points for an unpublished generation are never returned, because reads intersect index hits with published rows.
4. Query answers are cached in Redis for five minutes, keyed by tenant, published generation and question. A new publication changes the generation, so stale answers are not served.

## Validation status

Verified in this environment:

- The full stack built and started. Health, login through Keycloak, learning paths, queries, teacher waivers (record, effect on the path, revoke), advisor briefs with cited sources, and asynchronous ingestion through the worker all worked through the gateway.
- Vectors and graph nodes were written to Qdrant and Neo4j by the worker; retrieval read them back.
- The test suite passed offline, against live Qdrant and Neo4j, against a real OPA (Rego and in-process decisions agree), and against a real Keycloak realm (tokens accepted, tampered tokens rejected).
- The web app built and the teacher and advisor routes are served.

Not verified here:

- Amazon Bedrock generation and Titan embeddings were tested with mocks only. No AWS credentials were available.
- The portals were not exercised in a browser; they were compiled and served.
- The CI workflow has not run on GitHub.

## Production boundaries

These are deliberate limits of the pilot. Each one is listed in the architecture document as production work.

- Keycloak runs in `start-dev` mode with a development database. Production needs external PostgreSQL, production startup and backups.
- The password grant is for the demo login form only. Production uses Authorization Code with PKCE through the gateway, and short-lived sessions with revocation.
- Internal traffic is plain HTTP on a private network. Production needs TLS or mTLS between services and workload identity instead of the shared `SOURCE_SERVICE_TOKEN`.
- The source service must remain a single process owning one SQLite file on one host. Do not share that file over network storage or scale it horizontally.
- Named volumes are not backups and are not encrypted. Use SQLite online backups, Neo4j and Qdrant snapshot procedures, and separate identity backups.
- Secrets are environment variables in this compose file, which `docker inspect` can reveal. Production uses secret mounts or a secrets manager.
- There is no monitoring stack, log shipping, or alerting in this file. The health endpoints are liveness-level.
