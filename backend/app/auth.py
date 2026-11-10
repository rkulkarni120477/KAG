"""OIDC token validation against Keycloak, plus the development password grant used by /auth/login.

Validation checks signature (RS256 via the realm JWKS), issuer, audience, expiry and required
claims. The API then cross-checks the token's tenant, user and role against the source service.

Development note: /auth/login uses the Resource Owner Password grant so the existing login form works.
That grant is deprecated for production; production uses the Authorization Code + PKCE flow through
the gateway. Tokens are not revocable here, so keep the access-token lifetime short.
"""
import os
import threading

import httpx
import jwt
from jwt import PyJWKClient

DEFAULT_REALM = "http://keycloak:8080/auth/realms/education"


class TokenError(Exception):
    """The presented token is missing, malformed, expired or not issued for this application."""


class IdentityProviderUnavailable(RuntimeError):
    """Keycloak could not be reached or answered unexpectedly. Fail closed."""


def issuer() -> str:
    return os.getenv("OIDC_ISSUER", "https://localhost:8443/auth/realms/education")


def audience() -> str:
    return os.getenv("OIDC_AUDIENCE", "kag-api")


def client_id() -> str:
    return os.getenv("OIDC_CLIENT_ID", "kag-web")


def jwks_url() -> str:
    return os.getenv("OIDC_JWKS_URL", f"{DEFAULT_REALM}/protocol/openid-connect/certs")


def token_url() -> str:
    return os.getenv("OIDC_TOKEN_URL", f"{DEFAULT_REALM}/protocol/openid-connect/token")


_lock = threading.Lock()
_jwks: PyJWKClient | None = None
_key_resolver = None       # tests: callable(token) -> public key
_token_transport = None    # tests: httpx transport for the token endpoint


def _jwk_client() -> PyJWKClient:
    global _jwks
    with _lock:
        if _jwks is None:
            _jwks = PyJWKClient(jwks_url(), cache_keys=True, lifespan=300, timeout=5)
        return _jwks


def verify(token: str) -> dict:
    if not token:
        raise TokenError("missing token")
    try:
        key = _key_resolver(token) if _key_resolver else _jwk_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256"], audience=audience(), issuer=issuer(),
            options={"require": ["exp", "iss", "aud", "sub", "tenant_id", "kag_user_id", "role"]},
        )
    except jwt.PyJWTError as error:
        raise TokenError(str(error)) from error
    except Exception as error:  # a JWKS fetch failure must never be mistaken for a valid token
        raise TokenError(f"key resolution failed: {error}") from error


def password_grant(username: str, password: str) -> str | None:
    """Return an access token, or None for wrong credentials. Raises if the provider is unavailable."""
    try:
        with httpx.Client(timeout=5.0, transport=_token_transport) as http:
            response = http.post(token_url(), data={
                "grant_type": "password", "client_id": client_id(), "scope": "openid",
                "username": username, "password": password,
            })
    except httpx.HTTPError as error:
        raise IdentityProviderUnavailable(str(error)) from error
    if response.status_code in (400, 401):
        return None
    if response.status_code != 200:
        raise IdentityProviderUnavailable(f"token endpoint returned {response.status_code}")
    return response.json()["access_token"]


def configure_for_tests(key_resolver=None, token_transport=None) -> None:
    global _key_resolver, _token_transport
    _key_resolver = key_resolver
    _token_transport = token_transport
