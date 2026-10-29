"""HttpOnly browser session bridge for the LeadX portal.

The browser never persists Supabase access/refresh tokens in JavaScript-readable
storage.  The login UI may exchange a freshly-created Supabase session for
HttpOnly cookies; all subsequent CRM/admin API calls authenticate from those
cookies and use a double-submit CSRF token for state-changing requests.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.middleware.auth import (
    _verify_supabase_jwt,
    _local_claims,
    authenticate_local_credentials,
    issue_local_token,
    local_authentication_enabled,
    local_validation_mode,
    verify_local_token,
)
from app.middleware.rate_limit import check_rate_limit_async, client_ip

logger = logging.getLogger("lead_engine.session")
router = APIRouter(prefix="/auth/session", tags=["session"])

ACCESS_COOKIE = "leadx_session"
REFRESH_COOKIE = "leadx_refresh"
CSRF_COOKIE = "leadx_csrf"
COOKIE_PATH = "/"
COOKIE_MAX_AGE = 3600
REFRESH_MAX_AGE = 60 * 60 * 24 * 30


class SessionExchange(BaseModel):
    # Supabase exchange fields remain supported for hardened/provider-backed
    # deployments. Local validation uses only the explicit synthetic credentials.
    access_token: str | None = None
    refresh_token: str | None = None
    email: str | None = None
    password: str | None = None


def _prod() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in {"production", "prod", "staging"}


def _cookie_secure() -> bool:
    return _prod() or os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}


def _set_cookie(response: Response, name: str, value: str, *, max_age: int, httponly: bool) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=httponly,
        secure=_cookie_secure(),
        samesite="lax",
        path=COOKIE_PATH,
    )


def _clear_cookie(response: Response, name: str, *, httponly: bool) -> None:
    response.set_cookie(key=name, value="", max_age=0, expires=0, httponly=httponly,
                        secure=_cookie_secure(), samesite="lax", path=COOKIE_PATH)
def _supabase_url() -> str:
    if local_validation_mode():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Provider isolation is enabled")
    value = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not value:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured")
    return value


def _anon_key() -> str:
    value = os.getenv("SUPABASE_ANON_KEY", "")
    if not value:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured")
    return value


def _local_user_response(claims: dict) -> dict:
    return {
        "id": claims.get("sub"),
        "email": claims.get("email"),
        # The local issuer is trusted only inside local validation. This
        # marker lets the browser skip the external verification-code loop;
        # it is not an externally verified email timestamp.
        "email_confirmed_at": "local-validation",
        "local_validation": True,
    }


async def _establish_local_session(request: Request, response: Response, body: SessionExchange):
    """Issue an offline synthetic session; no provider or email call is made."""
    if not local_authentication_enabled():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    email = (body.email or "").strip().lower()
    identity = hashlib.sha256(f"{client_ip(request)}|{email}".encode("utf-8", "replace")).hexdigest()[:32]
    local_rate_limit = max(10, min(int(os.getenv("LOCAL_AUTH_RATE_LIMIT", "60")), 1000))
    await check_rate_limit_async(identity, "local_authentication", local_rate_limit, 300)
    claims = authenticate_local_credentials(email, body.password or "")
    access = issue_local_token(claims)
    refresh = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    _set_cookie(response, ACCESS_COOKIE, access, max_age=COOKIE_MAX_AGE, httponly=True)
    _set_cookie(response, REFRESH_COOKIE, refresh, max_age=REFRESH_MAX_AGE, httponly=True)
    _set_cookie(response, CSRF_COOKIE, csrf, max_age=REFRESH_MAX_AGE, httponly=False)
    return {"ok": True, "user": _local_user_response(claims)}


@router.post("")
async def establish_session(request: Request, response: Response, body: SessionExchange):
    """Establish a local synthetic or provider-backed HttpOnly session."""
    if local_validation_mode():
        # Local mode has a hard provider boundary: access tokens and refresh
        # tokens from Supabase are not accepted or exchanged here.
        return await _establish_local_session(request, response, body)
    if not body.access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    try:
        payload = await _verify_supabase_jwt(body.access_token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    if not payload.get("sub"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")

    # Do not accept an arbitrary refresh token without a valid matching access token.
    csrf = secrets.token_urlsafe(32)
    _set_cookie(response, ACCESS_COOKIE, body.access_token, max_age=COOKIE_MAX_AGE, httponly=True)
    if body.refresh_token:
        _set_cookie(response, REFRESH_COOKIE, body.refresh_token, max_age=REFRESH_MAX_AGE, httponly=True)
    _set_cookie(response, CSRF_COOKIE, csrf, max_age=REFRESH_MAX_AGE, httponly=False)
    return {"ok": True, "user": {"id": payload.get("sub"), "email": payload.get("email"),
                                   "email_confirmed_at": payload.get("email_confirmed_at")}}


@router.get("")
async def current_session(request: Request):
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active session")
    try:
        payload = await _verify_supabase_jwt(token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    return {"ok": True, "user": _local_user_response(payload) if payload.get("iss") == "leadx-local" else {"id": uid, "email": payload.get("email"),
                                  "email_confirmed_at": payload.get("email_confirmed_at")}}


@router.post("/refresh")
async def refresh_session(request: Request, response: Response):
    if local_validation_mode():
        # Refresh local JWTs offline; never call the Supabase Auth API from the
        # local provider-isolated profile.
        access = request.cookies.get(ACCESS_COOKIE)
        if not access:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session refresh failed")
        claims = verify_local_token(access)
        rotated = issue_local_token(_local_claims(str(claims.get("email") or "")))
        _set_cookie(response, ACCESS_COOKIE, rotated, max_age=COOKIE_MAX_AGE, httponly=True)
        _set_cookie(response, REFRESH_COOKIE, secrets.token_urlsafe(32), max_age=REFRESH_MAX_AGE, httponly=True)
        _set_cookie(response, CSRF_COOKIE, secrets.token_urlsafe(32), max_age=REFRESH_MAX_AGE, httponly=False)
        return {"ok": True, "user": _local_user_response(claims)}

    refresh = request.cookies.get(REFRESH_COOKIE)
    if not refresh:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh session")
    url = f"{_supabase_url()}/auth/v1/token?grant_type=refresh_token"
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.post(url, headers={"apikey": _anon_key(), "Content-Type": "application/json"},
                              json={"refresh_token": refresh})
    if r.status_code >= 300:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session refresh failed")
    data = r.json() or {}
    access = str(data.get("access_token") or "")
    new_refresh = str(data.get("refresh_token") or refresh)
    if not access:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session refresh failed")
    payload = await _verify_supabase_jwt(access)
    _set_cookie(response, ACCESS_COOKIE, access, max_age=int(data.get("expires_in") or COOKIE_MAX_AGE), httponly=True)
    _set_cookie(response, REFRESH_COOKIE, new_refresh, max_age=REFRESH_MAX_AGE, httponly=True)
    _set_cookie(response, CSRF_COOKIE, secrets.token_urlsafe(32), max_age=REFRESH_MAX_AGE, httponly=False)
    return {"ok": True, "user": {"id": payload.get("sub"), "email": payload.get("email"),
                                  "email_confirmed_at": payload.get("email_confirmed_at")}}


@router.delete("")
async def clear_session(response: Response):
    _clear_cookie(response, ACCESS_COOKIE, httponly=True)
    _clear_cookie(response, REFRESH_COOKIE, httponly=True)
    _clear_cookie(response, CSRF_COOKIE, httponly=False)
    return {"ok": True}
