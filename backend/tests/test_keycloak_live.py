"""Live Keycloak test: the realm issues tokens that the API validates through the realm's JWKS.

Skipped unless KEYCLOAK_TOKEN_URL and OIDC_JWKS_URL are set, e.g.
  KEYCLOAK_TOKEN_URL=http://localhost:8080/auth/realms/education/protocol/openid-connect/token
  OIDC_JWKS_URL=http://localhost:8080/auth/realms/education/protocol/openid-connect/certs
The realm's public issuer (https://localhost:8443/auth/realms/education) is what tokens carry.
"""
import os

import httpx
import pytest

from app import auth

TOKEN_URL = os.getenv("KEYCLOAK_TOKEN_URL", "")
JWKS_URL = os.getenv("OIDC_JWKS_URL", "")
pytestmark = pytest.mark.skipif(not (TOKEN_URL and JWKS_URL), reason="Keycloak live settings not set")


@pytest.fixture
def live_auth(monkeypatch):
    # Use the real JWKS for validation and the real token endpoint for the password grant.
    auth.configure_for_tests(key_resolver=None, token_transport=None)
    monkeypatch.setenv("OIDC_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("OIDC_ISSUER", "https://localhost:8443/auth/realms/education")  # the realm's public issuer
    auth._jwks = None
    yield
    auth.configure_for_tests(key_resolver=lambda token: __import__("helpers").PUBLIC_PEM,
                             token_transport=httpx.MockTransport(__import__("helpers").token_endpoint))


def test_realm_token_is_accepted_and_carries_kag_claims(live_auth):
    token = auth.password_grant("teacher@northstar.demo", "demo-pass-2026")
    assert token
    claims = auth.verify(token)
    assert claims["aud"] == "kag-api"
    assert claims["tenant_id"] == "northstar"
    assert claims["kag_user_id"] == "teacher"
    assert claims["role"] == "teacher"


def test_wrong_password_is_refused_by_the_realm(live_auth):
    assert auth.password_grant("teacher@northstar.demo", "not-the-password") is None


def test_tampered_realm_token_is_rejected(live_auth):
    token = auth.password_grant("student@northstar.demo", "demo-pass-2026")
    header, payload, signature = token.split(".")
    forged_payload = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    with pytest.raises(auth.TokenError):
        auth.verify(f"{header}.{forged_payload}.{signature}")
