"""Shared test harness.

- One temporary data directory and one in-process source service for the whole session.
- A test RSA key: the token endpoint issues real RS256 JWTs and the API validates them with the same key.
- A fake Keycloak token endpoint (httpx.MockTransport) that checks demo passwords like the real realm.
- Offline defaults: no Redis, inline queue, no web search, no OPA, no AWS.
"""
import json
import os
import tempfile
import time

_DATA_DIR = tempfile.mkdtemp(prefix="kag-test-")
os.environ.update({
    "KAG_DATA_DIR": _DATA_DIR,
    "KAG_LOAD_DOTENV": "false",
    "SOURCE_SERVICE_TOKEN": "test-source-token",
    "SOURCE_URL": "http://source.test",
    "OIDC_ISSUER": "https://kc.test/auth/realms/education",
    "OIDC_AUDIENCE": "kag-api",
    "OIDC_CLIENT_ID": "kag-web",
    "KAG_QUEUE_MODE": "inline",
    "WEB_SEARCH_ENABLED": "false",
    "KAG_DEMO_PASSWORD": "demo-pass-2026",
    "KAG_RATE_LIMIT_API": "100000",
    "KAG_RATE_LIMIT_LOGIN": "100000",
})
# Index backends and their URLs are inherited from the environment, so the same suite can run against
# live Qdrant and Neo4j (KAG_VECTOR_BACKEND=qdrant KAG_GRAPH_BACKEND=neo4j). Offline is the default.
os.environ.setdefault("KAG_VECTOR_BACKEND", "sqlite")
os.environ.setdefault("KAG_GRAPH_BACKEND", "sqlite")
for _name in ("OPA_URL", "BEDROCK_MODEL_ID", "BEDROCK_EMBED_MODEL_ID", "REDIS_URL"):
    os.environ.pop(_name, None)

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, runtime
from app.source.main import app as source_app
from app.source_client import SourceClient, set_source

import helpers  # noqa: E402  (after environment setup)

SOURCE_TOKEN = os.environ["SOURCE_SERVICE_TOKEN"]

# Source service: in-process, lifespan (schema and seed) entered for the whole session.
_source_http = TestClient(source_app, base_url="http://source.test", headers={"X-Service-Token": SOURCE_TOKEN})
_source_http.__enter__()
set_source(SourceClient(_source_http))

auth.configure_for_tests(key_resolver=lambda token: helpers.PUBLIC_PEM, token_transport=httpx.MockTransport(helpers.token_endpoint))
runtime.set_redis(None)


@pytest.fixture
def source_token():
    return SOURCE_TOKEN
