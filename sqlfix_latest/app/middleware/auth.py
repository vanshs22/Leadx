"""
JWT auth middleware for Lead Engine + CRM.
Resolves tenant from verified token — NEVER trust tenant_id from the URL alone.

Usage:
  from app.middleware.auth import require_tenant, TenantContext

  @router.get("/crm/dashboard")
  async def dashboard(ctx: TenantContext = Depends(require_tenant)):
      tid = ctx.tenant_id  # verified membership
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("lead_engine.auth")
_bearer = HTTPBearer(auto_error=False)

# Dev bypass only when explicitly enabled AND not running in production.
#
# This flag grants owner-role access to ANY tenant on an unauthenticated request
# carrying an X-Tenant-Id header — a complete tenant-isolation bypass. start.sh
# writes it into .env by default, so relying on operators to unset it before
# deploying is not a safety model. It is now hard-disabled whenever
# ENVIRONMENT is production/staging, regardless of how the flag is set.
_ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
_IS_PRODUCTION = _ENVIRONMENT in ("production", "prod", "staging")
_BYPASS_REQUESTED = os.getenv("LEAD_ENGINE_DEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes")
ALLOW_DEV_BYPASS = _BYPASS_REQUESTED and not _IS_PRODUCTION

if _BYPASS_REQUESTED and _IS_PRODUCTION:
    logger.critical(
        "LEAD_ENGINE_DEV_AUTH_BYPASS is set but ENVIRONMENT=%s — bypass FORCE-DISABLED. "
        "Remove the flag from your environment.",
        _ENVIRONMENT,
    )
elif ALLOW_DEV_BYPASS:
    logger.warning(
        "LEAD_ENGINE_DEV_AUTH_BYPASS enabled (env=%s) — unauthenticated requests with "
        "an X-Tenant-Id header get owner access. NEVER use this outside local dev.",
        _ENVIRONMENT,
    )


@dataclass
class TenantContext:
    tenant_id: str
    user_id: str
    role: str  # owner | admin | member | viewer
    email: Optional[str] = None


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


# Shared async HTTP client + short-lived caches keep the login hot path fast and
# prevent one httpx connection per request. Membership changes propagate within
# AUTH_MEMBERSHIP_CACHE_SECONDS (default 15s).
import asyncio
import time
import hashlib
from urllib.parse import quote
from collections import OrderedDict

_http_client = None
_http_client_lock = asyncio.Lock()
_jwks_cache = None
_jwks_expires_at = 0.0
_jwks_lock = asyncio.Lock()
_membership_cache: OrderedDict[tuple[str, str | None], tuple[float, Optional["TenantContext"]]] = OrderedDict()
_membership_locks: dict[tuple[str, str | None], asyncio.Lock] = {}
_MEMBERSHIP_CACHE_MAX = int(os.getenv("AUTH_MEMBERSHIP_CACHE_MAX", "10000"))
_MEMBERSHIP_LOCKS_MAX = 10000

def _membership_lock_for(key):
    lock = _membership_locks.get(key)
    if lock is None:
        lock = asyncio.Lock(); _membership_locks[key] = lock
        if len(_membership_locks) > _MEMBERSHIP_LOCKS_MAX:
            _membership_locks.pop(next(iter(_membership_locks)), None)
    return lock

def _cache_put(key, value):
    _membership_cache[key] = value
    _membership_cache.move_to_end(key)
    while len(_membership_cache) > _MEMBERSHIP_CACHE_MAX:
        _membership_cache.popitem(last=False)


def _service_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
        or ""
    )


def _service_headers(key: str, **extra: str) -> dict[str, str]:
    """Use modern Supabase secrets as apikey-only credentials."""
    headers = {"apikey": key, **extra}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def _get_http_client():
    global _http_client
    if _http_client is not None:
        return _http_client
    async with _http_client_lock:
        if _http_client is None:
            import httpx
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(3.0, connect=1.5),
                limits=httpx.Limits(max_connections=500, max_keepalive_connections=250),
                http2=False,
            )
    return _http_client


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_expires_at
    now = time.monotonic()
    if _jwks_cache and now < _jwks_expires_at:
        return _jwks_cache
    async with _jwks_lock:
        now = time.monotonic()
        if _jwks_cache and now < _jwks_expires_at:
            return _jwks_cache
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        if not supabase_url:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured")
        client = await _get_http_client()
        r = await client.get(f"{supabase_url}/auth/v1/.well-known/jwks.json")
        r.raise_for_status()
        _jwks_cache = r.json()
        _jwks_expires_at = time.monotonic() + 600
        return _jwks_cache


async def _verify_supabase_jwt(token: str) -> dict:
    """Verify Supabase JWT locally when possible; use pooled Auth API only as fallback."""
    from jose import jwt as jose_jwt, JWTError

    jwt_secret = os.getenv("SUPABASE_JWT_SECRET") or os.getenv("JWT_SECRET")
    if jwt_secret:
        try:
            return jose_jwt.decode(
                token, jwt_secret, algorithms=["HS256"],
                audience="authenticated", options={"verify_aud": True},
            )
        except Exception as e:
            logger.debug("HS256 JWT verification failed: %s", e)

    # Modern Supabase projects may use asymmetric signing keys. Cache JWKS so
    # the first request pays the network cost and subsequent logins verify locally.
    try:
        header = jose_jwt.get_unverified_header(token)
        kid = header.get("kid")
        alg = header.get("alg")
        allowed_algs = {"RS256", "ES256"}
        if kid and alg in allowed_algs:
            jwks = await _get_jwks()
            key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if key_data:
                return jose_jwt.decode(
                    token, key_data, algorithms=[alg],
                    audience="authenticated", options={"verify_aud": True},
                )
    except Exception as e:
        logger.debug("JWKS JWT verification unavailable, using Auth API fallback: %s", e)

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured")
    client = await _get_http_client()
    r = await client.get(
        f"{supabase_url}/auth/v1/user",
        headers={"Authorization": f"Bearer {token}", "apikey": anon_key},
    )
    if r.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    data = r.json()
    return {"sub": data.get("id"), "email": data.get("email"), **data}


async def _email_verified(payload: dict, token: str) -> bool:
    """Use an explicit verified claim; otherwise ask Supabase Auth."""
    if payload.get("email_verified") is True or payload.get("email_confirmed") is True or payload.get("email_confirmed_at"):
        return True
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "")
    if not url: return False
    try:
        r = await (await _get_http_client()).get(f"{url}/auth/v1/user", headers={"Authorization":f"Bearer {token}","apikey":anon})
        if r.status_code != 200: return False
        user = r.json() or {}
        return bool(user.get("email_confirmed_at") or user.get("email_verified"))
    except Exception:
        return False


async def _rest_membership(user_id: str, tenant_id: Optional[str], email: Optional[str]) -> list[dict]:
    """Async, pooled Supabase REST lookup for the auth hot path."""
    import httpx
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = _service_key()
    if not url or not key:
        return []
    params: list[tuple[str, str]] = [
        ("select", "id,tenant_id,role,email,status,auth_user_id"),
        ("limit", "5"),
    ]
    if tenant_id:
        params.append(("tenant_id", f"eq.{tenant_id}"))
    if user_id:
        params.append(("auth_user_id", f"eq.{user_id}"))
    client = await _get_http_client()
    try:
        r = await client.get(
            f"{url}/rest/v1/tenant_members",
            params=params,
            headers=_service_headers(key),
        )
        if r.status_code < 300:
            rows = r.json() or []
            active = [row for row in rows if (row.get("status") or "active") == "active"]
            return active or rows
    except Exception as e:
        logger.warning("Async tenant membership lookup failed: %s", e)
    return []


async def _resolve_membership(
    user_id: str,
    tenant_id: Optional[str] = None,
    email: Optional[str] = None,
    *,
    email_verified: bool = False,
) -> TenantContext:
    """Resolve membership without blocking FastAPI's event loop."""
    cache_key = (user_id, tenant_id)
    now = time.monotonic()
    cached = _membership_cache.get(cache_key)
    if cached and cached[0] > now:
        if cached[1] is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this workspace")
        return cached[1]

    # Stampede protection: many tabs/users hitting the same workspace at once
    # should share one membership lookup rather than creating a DB burst.
    async with _membership_lock_for(cache_key):
        now = time.monotonic()
        cached = _membership_cache.get(cache_key)
        if cached and cached[0] > now:
            if cached[1] is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this workspace")
            return cached[1]
        rows = [r for r in (await _rest_membership(user_id, tenant_id, email)) if str(r.get("auth_user_id") or "") == str(user_id)]
    if not rows and email:
        # Legacy migration path: an email may claim ONLY an unowned row, and
        # only after Supabase has confirmed the JWT email. Never return a row
        # already owned by another auth_user_id.
        if not email_verified:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Email verification required")
        url = os.getenv("SUPABASE_URL", "").rstrip("/")
        key = _service_key()
        if url and key:
            client = await _get_http_client()
            params: list[tuple[str, str]] = [
                ("select", "id,tenant_id,role,email,status,auth_user_id"),
                ("email", f"eq.{email.strip().lower()}"),
                ("auth_user_id", "is.null"),
                ("status", "eq.active"),
                ("limit", "5"),
            ]
            if tenant_id:
                params.append(("tenant_id", f"eq.{tenant_id}"))
            try:
                r = await client.get(
                    f"{url}/rest/v1/tenant_members", params=params,
                    headers=_service_headers(key),
                )
                if r.status_code < 300:
                    raw = r.json() or []
                    if raw:
                        claim = raw[0]
                        patch = await client.patch(
                            f"{url}/rest/v1/tenant_members",
                            params={"id": f"eq.{claim['id']}", "auth_user_id": "is.null"},
                            headers=_service_headers(key, **{"Content-Type": "application/json", "Prefer": "return=representation"}),
                            json={"auth_user_id": user_id},
                        )
                        if patch.status_code < 300 and patch.json():
                            rows = patch.json()
            except Exception:
                logger.warning("legacy membership claim failed", exc_info=True)

    if not rows:
        _cache_put(cache_key, (now + 5, None))
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this workspace")

    if tenant_id is None and len(rows) > 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Multiple workspaces found; select a tenant explicitly")
    row = rows[0]
    ctx = TenantContext(
        tenant_id=row["tenant_id"], user_id=user_id,
        role=row.get("role") or "member", email=row.get("email") or email,
    )
    _cache_put(cache_key, (now + float(os.getenv("AUTH_MEMBERSHIP_CACHE_SECONDS", "30")), ctx))
    return ctx


async def require_tenant(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> TenantContext:
    """
    Primary dependency: valid JWT + active tenant membership.
    Optional header X-Tenant-Id when user belongs to multiple tenants.
    """
    # Dev bypass — NEVER in production
    if ALLOW_DEV_BYPASS and not creds:
        dev_tenant = request.headers.get("X-Tenant-Id") or request.path_params.get("tenant_id")
        if dev_tenant:
            logger.warning("DEV AUTH BYPASS active for tenant %s", dev_tenant)
            return TenantContext(tenant_id=str(dev_tenant), user_id="dev", role="owner")

    token = creds.credentials if creds and creds.credentials else request.cookies.get("leadx_session")
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing authenticated session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = await _verify_supabase_jwt(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    # Prefer explicit tenant header; else path param; else first membership
    header_tenant = request.headers.get("X-Tenant-Id")
    path_tenant = request.path_params.get("tenant_id")
    preferred = header_tenant or path_tenant

    # SECURITY: path tenant_id is only a HINT — membership is verified
    email = payload.get("email")
    verified = await _email_verified(payload, token)
    ctx = await _resolve_membership(user_id, preferred if preferred else None, email=email, email_verified=verified)

    # If path claimed a different tenant, reject
    if path_tenant and str(path_tenant) != ctx.tenant_id:
        # User might be member of path tenant — re-resolve
        try:
            ctx = await _resolve_membership(user_id, str(path_tenant), email=email, email_verified=verified)
        except HTTPException:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied to this tenant")

    # Attach for downstream logging
    request.state.tenant = ctx
    return ctx


@dataclass
class AuthedUser:
    user_id: str
    email: Optional[str] = None


async def require_auth_only(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthedUser:
    """
    Lightweight dependency: valid Supabase JWT, no tenant-membership check.
    Use only for pre-membership actions (e.g. accepting a team invite) —
    everything else should use require_tenant.
    """
    if ALLOW_DEV_BYPASS and not creds:
        logger.warning("DEV AUTH BYPASS active for require_auth_only")
        return AuthedUser(user_id="dev", email="dev@local")
    # Cookie sessions are accepted for browser flows; machine clients may still use Bearer.
    token = creds.credentials if creds and creds.credentials else request.cookies.get("leadx_session")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing authenticated session", headers={"WWW-Authenticate": "Bearer"})
    payload = await _verify_supabase_jwt(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return AuthedUser(user_id=user_id, email=payload.get("email"))


async def require_admin(ctx: TenantContext = Depends(require_tenant)) -> TenantContext:
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return ctx


def assert_tenant_match(ctx: TenantContext, tenant_id: str | UUID) -> None:
    """Call inside handlers that still accept tenant_id in path for URL design."""
    if str(tenant_id) != ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tenant mismatch")
