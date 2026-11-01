"""Fake Keycloak for the mock CKW OpenEnergy API.

CKW protects the OpenEnergy API with Keycloak in the ``CKW-prod`` realm. This module
reproduces just enough of that realm for offline integration work:

* ``GET  /realms/CKW-prod/protocol/openid-connect/auth`` - no login form, instant
  consent: 302 back to ``redirect_uri`` with a fresh ``code`` and the echoed ``state``.
* ``POST /realms/CKW-prod/protocol/openid-connect/token`` - ``authorization_code``,
  ``refresh_token`` and the password/OTP variant (``grant_type=password`` with
  ``code``); 400 for anything else, 403 for ``password`` without a ``code``.
* ``POST /realms/CKW-prod/otp/login`` - 204, records the OTP for one token exchange.

Tokens are JWT *shaped* (``header.payload.signature``) but unsigned: the header says
``"alg": "none"``, the signature is the fixed string ``mock-unsigned`` and nothing
verifies them. That is deliberate - the mock must never look like a real credential.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Final

from aiohttp import web

REALM: Final[str] = "CKW-prod"
REALM_PREFIX: Final[str] = f"/realms/{REALM}"
AUTHORIZE_PATH: Final[str] = f"{REALM_PREFIX}/protocol/openid-connect/auth"
TOKEN_PATH: Final[str] = f"{REALM_PREFIX}/protocol/openid-connect/token"
OTP_LOGIN_PATH: Final[str] = f"{REALM_PREFIX}/otp/login"

ACCESS_TOKEN_TTL: Final[int] = 600
REFRESH_TOKEN_TTL: Final[int] = 21600
MOCK_SIGNATURE: Final[str] = (
    base64.urlsafe_b64encode(b"mock-unsigned").rstrip(b"=").decode("ascii")
)
SUBJECT: Final[str] = "mock-customer"


@dataclass
class MockIdentityProvider:
    """In-memory state of the fake IdP (codes, refresh tokens, pending OTPs)."""

    codes: set[str] = field(default_factory=set)
    refresh_tokens: set[str] = field(default_factory=set)
    otps: set[str] = field(default_factory=set)


IDP: Final[web.AppKey[MockIdentityProvider]] = web.AppKey(
    "mock_idp", MockIdentityProvider
)


def _b64url(payload: bytes) -> str:
    """Base64url encode without padding, as JOSE does."""
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _jwt(token_type: str, lifetime: int) -> str:
    """Build an unsigned, JWT-shaped token. Not verifiable, by design."""
    issued_at = int(time.time())
    header = _b64url(
        json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    payload = _b64url(
        json.dumps(
            {
                "iss": f"http://mock-keycloak{REALM_PREFIX}",
                "aud": "account",
                "azp": "ckw-dev-mock",
                "sub": SUBJECT,
                "typ": token_type,
                "iat": issued_at,
                "exp": issued_at + lifetime,
                "jti": secrets.token_hex(8),
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{header}.{payload}.{MOCK_SIGNATURE}"


def _oauth_error(status: int, error: str, description: str) -> web.Response:
    """Build an OAuth2 error body (the shape Keycloak returns)."""
    return web.json_response(
        {"error": error, "error_description": description}, status=status
    )


async def _read_payload(request: web.Request) -> dict[str, str]:
    """Read a JSON or form payload as a flat string dictionary."""
    if request.content_type == "application/json":
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}
    form = await request.post()
    return {str(key): str(value) for key, value in form.items()}


async def handle_authorize(request: web.Request) -> web.StreamResponse:
    """Redirect straight back with an authorization code and the echoed state."""
    redirect_uri = request.query.get("redirect_uri")
    if not redirect_uri:
        return _oauth_error(400, "invalid_request", "redirect_uri is required")

    code = secrets.token_urlsafe(24)
    request.app[IDP].codes.add(code)

    separator = "&" if "?" in redirect_uri else "?"
    target = f"{redirect_uri}{separator}code={code}"
    state = request.query.get("state")
    if state:
        target = f"{target}&state={state}"
    return web.Response(status=302, headers={"Location": target})


async def handle_token(request: web.Request) -> web.StreamResponse:
    """Exchange an authorization code, a refresh token or an OTP for tokens."""
    payload = await _read_payload(request)
    grant_type = payload.get("grant_type", "")
    code = payload.get("code", "").strip()
    idp = request.app[IDP]

    if grant_type == "authorization_code":
        if not code:
            return _oauth_error(400, "invalid_request", "code is required")
        if code not in idp.codes:
            return _oauth_error(400, "invalid_grant", "unknown or already used code")
        idp.codes.discard(code)
    elif grant_type == "password":
        if not code:
            return _oauth_error(
                403,
                "access_denied",
                f"the OTP variant needs the code from /realms/{REALM}/otp/login",
            )
        if code not in idp.otps:
            return _oauth_error(400, "invalid_grant", "unknown or already used OTP")
        idp.otps.discard(code)
    elif grant_type != "refresh_token":
        return _oauth_error(
            400,
            "unsupported_grant_type",
            "expected authorization_code, refresh_token or password",
        )

    access_token = _jwt("Bearer", ACCESS_TOKEN_TTL)
    refresh_token = _jwt("Refresh", REFRESH_TOKEN_TTL)
    idp.refresh_tokens.add(refresh_token)
    return web.json_response(
        {
            "access_token": access_token,
            "expires_in": ACCESS_TOKEN_TTL,
            "refresh_token": refresh_token,
            "refresh_expires_in": REFRESH_TOKEN_TTL,
            "token_type": "Bearer",
        }
    )


async def handle_otp_login(request: web.Request) -> web.StreamResponse:
    """Accept an OTP (or invent one) and remember it for a single token exchange."""
    payload = await _read_payload(request)
    otp = (
        payload.get("code") or payload.get("otp") or payload.get("password") or ""
    ).strip()
    otp = otp or f"{secrets.randbelow(10**6):06d}"
    request.app[IDP].otps.add(otp)
    return web.Response(status=204, headers={"x-mock-otp": otp})


def register_keycloak_routes(app: web.Application) -> None:
    """Register the fake IdP routes and reset its in-memory state."""
    app[IDP] = MockIdentityProvider()
    app.router.add_get(AUTHORIZE_PATH, handle_authorize)
    app.router.add_post(TOKEN_PATH, handle_token)
    app.router.add_post(OTP_LOGIN_PATH, handle_otp_login)
