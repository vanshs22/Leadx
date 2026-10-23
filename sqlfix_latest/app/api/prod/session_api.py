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

from app.middleware.auth import _verify_supabase_jwt

logger = logging.getLogger("lead_engine.session")
router = APIRouter(prefix="/auth/session", tags=["session"])

ACCESS_COOKIE = "leadx_session"
REFRESH_COOKIE = "leadx_refresh"
CSRF_COOKIE = "leadx_csrf"
COOKIE_PATH = "/"
COOKIE_MAX_AGE = 3600
REFRESH_MAX_AGE = 60 * 60 * 24 * 30


class SessionExchange(BaseModel):
    access_token: str
    refresh_token: str | None = None


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
    value = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not value:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured")
    return value


def _anon_key() -> str:
    value = os.getenv("SUPABASE_ANON_KEY", "")
    if not value:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured")
    return value


@router.post("")
async def establish_session(request: Request, response: Response, body: SessionExchange):
    """Exchange a fresh Supabase session for HttpOnly LeadX cookies."""
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
    return {"ok": True, "user": {"id": uid, "email": payload.get("email"),
                                  "email_confirmed_at": payload.get("email_confirmed_at")}}


@router.post("/refresh")
async def refresh_session(request: Request, response: Response):
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
