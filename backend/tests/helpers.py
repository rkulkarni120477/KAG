"""Helpers shared by tests. Import only after conftest has set the environment."""
import os
import time
from urllib.parse import unquote_plus

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.source.db import db

PASSWORD = "demo-pass-2026"
_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
PUBLIC_PEM = _private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

# Identities mirror the source seed: email -> (user id, tenant, role).
IDENTITIES = {
    "student@northstar.demo": ("student", "northstar", "student"),
    "teacher@northstar.demo": ("teacher", "northstar", "teacher"),
    "advisor@northstar.demo": ("advisor", "northstar", "advisor"),
    "admin@northstar.demo": ("admin", "northstar", "admin"),
    "isolation@other.demo": ("outsider", "other", "teacher"),
}


def mint(user_id: str, tenant: str, role: str, *, expires_in: int = 900, audience: str = "kag-api",
         issuer: str | None = None, private_pem: bytes = PRIVATE_PEM) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer or os.environ["OIDC_ISSUER"], "aud": audience, "sub": f"uuid-{user_id}",
        "iat": now, "exp": now + expires_in, "kag_user_id": user_id, "tenant_id": tenant, "role": role,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256")


def token_endpoint(request: httpx.Request) -> httpx.Response:
    """Fake Keycloak password grant with the same success and failure statuses as the realm."""
    form = {k: unquote_plus(v) for k, v in (item.split("=", 1) for item in request.content.decode().split("&"))}
    email, password = form.get("username", ""), form.get("password", "")
    if email not in IDENTITIES or password != PASSWORD:
        return httpx.Response(401, json={"error": "invalid_grant"})
    user_id, tenant, role = IDENTITIES[email]
    return httpx.Response(200, json={"access_token": mint(user_id, tenant, role), "token_type": "Bearer"})


def sql(statement: str, params=()):
    with db() as c:
        return c.execute(statement, params)


def query_one(statement: str, params=()):
    with db() as c:
        row = c.execute(statement, params).fetchone()
    return dict(row) if row else None


def query_all(statement: str, params=()):
    with db() as c:
        return [dict(r) for r in c.execute(statement, params).fetchall()]
