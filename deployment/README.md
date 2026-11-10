# Local Docker Compose blueprint

This describes container topology; it does not include application images or a working platform. Default services are Qdrant, Neo4j Community, Redis, development Keycloak and deny-by-default OPA. The `app` profile adds implementation-dependent services. Monitoring is specified in the architecture and must be added to the implementation with reviewed collector/Prometheus/Grafana configurations; this file does not pretend to deploy it.

## Prerequisites and setup

1. Use a Docker Compose v2 Linux-container environment and an encrypted host disk. Budget memory for graph, vector, embeddings and identity, then measure usage; do not treat a developer laptop as a sizing reference.
2. Copy `.env.example` to `.env`, then replace **all** image placeholders with valid, reviewed image digests and all credentials with local random secrets. OPA must support Rego v1; use Redis 8 under the reviewed license option. Digest placeholders deliberately do not resolve to runnable images.
3. Set an account-enabled Bedrock model/region. Application profiles need approved short-lived AWS credentials through a credential-process/workload mechanism configured in their images; `.env` deliberately contains no AWS keys. Browser bundles must never contain credentials.
4. Implement/build source, API, worker and web images according to `implementation/STRUCTURE.md`. Source listens on 8001, API on 8000, Next.js on 3000; images must honor the configured URLs and startup commands. Source owns migrations and SQLite; other containers never mount that file. Gate startup on real readiness with retry/backoff; `service_started` alone is not readiness.
5. Provision the Keycloak `education` realm, OIDC clients, audiences, PKCE redirects, workload identities and role/membership mappings. Publish tested OPA bundles. No protected application access works with the default deny policy.
6. Resolve public issuer versus internal discovery: `https://localhost:8443` is a browser issuer, **not** a usable loopback URL from another container. Supply a reachable internal JWKS/discovery URL with the same verified issuer, or container DNS/host mappings for a development hostname reachable by browser and containers. Trust the local Caddy CA in both browser and server trust stores; never disable certificate verification. BFF session encryption keys/client secrets must be injected before application startup.

From this directory, after the prerequisites:

```powershell
Copy-Item .env.example .env
# Edit .env with valid digests/secrets before continuing.
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d
# After building images, configuring identity, and publishing policy:
docker compose --env-file .env --profile app up -d
```

`Copy-Item` is a one-time setup step; preserve any existing `.env`. Do not print `docker compose config` without `--quiet`, because resolved configuration contains local secrets. No containers were started as part of producing this package.

## Development boundaries

- Only the TLS gateway publishes a loopback port. Indexes, queue, policy and SQLite source are private Docker services. Internal HTTP/Bolt/Redis links are **synthetic-data development only**. Add authenticated TLS/mTLS service gateways and client certificates before real records; AWS service discovery is not encryption.
- Keycloak `start-dev` and its development database are not production configuration. Use production startup, external PostgreSQL, appropriate hostname/proxy settings and backup procedures on AWS.
- This blueprint shows environment variables for readability. Docker access can reveal them. Production uses secret mounts/Secrets Manager with application-supported file loading and rotates credentials.
- Named volumes survive container replacement; they are not backups and do not imply at-rest encryption. Use SQLite online backup; offline Neo4j dumps/rebuild and supported Qdrant snapshots; separately back up identity state and documents.
- Gateway is in the `app` profile, so a dependency-only startup has no host web interface. Use in-network admin tooling for dependency smoke tests; do not expose unauthenticated ports for convenience.
- API's public health endpoint is liveness. Implement internal readiness, broker probes, worker heartbeat, ingestion lag and scheduled backup checks. Healthcheck commands require Python/cypher-shell in their respective images; validate exact commands against pinned images.
- OPA controls logical access; network segmentation and authentication for the source/index services remain required. Application clients must construct scoped queries and refuse empty/invalid policy decisions.
- Scale source-service to **one process owning the SQLite file**. Scale API/workers only within measured capacity. Never share that file over EFS/NFS or between distributed tasks.
- Add `.env`, downloaded data, certificates and secrets to your implementation's ignore rules. Stop services with `docker compose down`; avoid volume deletion when retaining data.

## Validation status

The Compose file was statically validated with Compose configuration parsing using non-secret dummy values. Application images, registry image digests, realm bootstrapping, internal TLS, runtime credentials, dependency readiness and actual end-to-end behavior still require implementation and testing. The typed Python examples were syntax-checked, not executed against Bedrock.
