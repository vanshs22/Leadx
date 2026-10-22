"""
Lead Engine — managed service entrypoint.
  uvicorn app.app_service:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import re
import os
from pathlib import Path as _Path

# Load lead-engine-v2/.env before anything else reads os.environ
try:
    from dotenv import load_dotenv
    _env_file = _Path(__file__).resolve().parents[1] / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file, override=False)
    else:
        load_dotenv(override=False)
except ImportError:
    pass

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import uuid
import secrets
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.middleware.rate_limit import check_rate_limit_async, client_ip
from app.layers.telemetry import (
    emit as emit_telemetry,
    error_code as telemetry_error_code,
    public_error as safe_public_error,
    request_id as validated_request_id,
    validation_details as safe_validation_details,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("lead_engine.service")

from app.api.prod import competitive_api
from app.api.prod import session_api
from app.api.prod import platform_api

app = FastAPI(
    title="Automiqo Lead Service",
    version="15.8.1",
    description="Managed lead generation — operator API + client CRM",
)

app.include_router(competitive_api.router)
app.include_router(session_api.router)
app.include_router(platform_api.router)

# Development: allow all origins (Codespaces / local). Production: set CORS_ORIGINS.
_env = os.getenv("ENVIRONMENT", "development").lower()
_cors = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
_IS_PROD_ENV = _env in ("production", "prod", "staging")
if not _IS_PROD_ENV and not _cors:
    _cors = ["*"]  # local / Codespaces convenience only
elif _IS_PROD_ENV and (not _cors or "*" in _cors):
    # Fail safe: never expose a credentialed API to every origin in production.
    # The Next.js app proxies /backend/* same-origin, so an empty list is fine.
    logger.warning("CORS_ORIGINS missing/wildcard in production — cross-origin browser access disabled.")
    _cors = [o for o in _cors if o != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=(_cors != ["*"]),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "X-Service-Key", "X-Request-Id", "Idempotency-Key", "X-CSRF-Token"],
    expose_headers=["X-Request-Id", "Retry-After"],
    max_age=600,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """Require double-submit CSRF proof when browser auth uses HttpOnly cookies."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.url.path.startswith(("/auth/session", "/billing/webhook", "/crm/webhooks/", "/webhooks/")):
        cookie_token = request.cookies.get("leadx_session")
        bearer = request.headers.get("authorization", "")
        if cookie_token and not bearer:
            csrf_cookie = request.cookies.get("leadx_csrf") or ""
            csrf_header = request.headers.get("x-csrf-token") or ""
            if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
                return JSONResponse(status_code=403, content={"ok": False, "error": "CSRF validation failed", "request_id": getattr(request.state, "request_id", None)})
    return await call_next(request)


@app.middleware("http")
async def idempotency_middleware(request: Request, call_next):
    """Durable idempotency for mutating JSON requests.

    A key is scoped to the authenticated session and exact request body. Only
    successful 2xx JSON responses are replayable. Failed/4xx responses release
    the claim, and stale processing claims can be reclaimed by the DB RPC.
    """
    from fastapi.responses import Response
    from app.services.aio import offload
    from app.integrations.prod.scale_control import claim_idempotency, finish_idempotency, release_idempotency
    import hashlib

    method = request.method.upper()
    key = (request.headers.get("idempotency-key") or "").strip()
    path = request.url.path
    exempt = path.startswith(("/billing/webhook", "/docs", "/redoc", "/openapi"))
    if method in {"GET", "HEAD", "OPTIONS"} or exempt or not key:
        return await call_next(request)
    if len(key) > 200 or not re.fullmatch(r"[A-Za-z0-9._:\-]{8,200}", key):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid Idempotency-Key", "request_id": getattr(request.state, "request_id", None)})

    body = await request.body()
    body_hash = hashlib.sha256(body).hexdigest()
    auth = request.headers.get("authorization", "")
    # The bearer token is never stored; only a stable digest scopes the key to
    # the authenticated session. Tenant/path are included to prevent cross-route reuse.
    scope_hash = hashlib.sha256((auth + "|" + (request.headers.get("x-tenant-id") or "") + "|" + path).encode()).hexdigest()
    operation = f"HTTP:{method}:{path}"
    tenant_id = request.headers.get("x-tenant-id") or request.path_params.get("tenant_id") or None
    acquired, existing = await offload(claim_idempotency, key, operation, tenant_id=tenant_id, scope_hash=scope_hash, request_hash=body_hash)
    if not acquired:
        if isinstance(existing, dict) and existing.get("idempotency_mismatch"):
            return JSONResponse(status_code=409, content={"ok": False, "error": "Idempotency-Key was already used with a different request or session.", "request_id": getattr(request.state, "request_id", None)})
        if isinstance(existing, dict) and "status_code" in existing and "body" in existing:
            return JSONResponse(status_code=int(existing.get("status_code") or 200), content=existing.get("body") or {}, headers={"X-Idempotency-Replay": "true"})
        return JSONResponse(status_code=409, content={"ok": False, "error": "This request is already being processed. Reuse the original result instead of submitting it again.", "request_id": getattr(request.state, "request_id", None)}, headers={"Retry-After": "2"})

    try:
        response = await call_next(request)
        body_out = b""
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                body_out += chunk
        content_type = (response.headers.get("content-type") or "").lower()
        # Cache only successful JSON mutations. 4xx/5xx responses are released
        # so validation/transient failures never poison the key forever.
        if "application/json" in content_type and len(body_out) <= 512 * 1024 and 200 <= response.status_code < 300:
            import json
            try:
                parsed = json.loads(body_out.decode("utf-8")) if body_out else {}
            except Exception:
                parsed = {"ok": True, "raw": body_out.decode("utf-8", "replace")[:2000]}
            stored = {"status_code": response.status_code, "body": parsed}
            await offload(finish_idempotency, key, operation, stored, "completed", scope_hash=scope_hash, request_hash=body_hash)
            return Response(content=body_out, status_code=response.status_code, headers=dict(response.headers), media_type=response.media_type)

        await offload(release_idempotency, key, operation, scope_hash=scope_hash)
        return Response(content=body_out, status_code=response.status_code, headers=dict(response.headers), media_type=response.media_type)
    except Exception:
        try:
            await offload(release_idempotency, key, operation, scope_hash=scope_hash)
        except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="database", outcome="optional_failure", error_code=telemetry_error_code(exc))
        raise


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Only accept sane client ids — prevents log/header injection while
    # preserving a valid upstream correlation ID end-to-end.
    request_id = validated_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    import time as _time
    _t0 = _time.perf_counter()
    try:
        # Protect public HTTP surfaces against accidental polling loops and
        # abusive bursts. Expensive pipeline/key endpoints have tighter,
        # endpoint-specific limits below; this is only the general API guard.
        if request.url.path.startswith(("/crm/", "/leads/v2")) and request.method != "OPTIONS":
            auth = request.headers.get("authorization", "") or request.cookies.get("leadx_session", "")
            import hashlib
            identity = client_ip(request) + ":" + hashlib.sha256(auth.encode()).hexdigest()[:16]
            await check_rate_limit_async(identity, "api", int(os.getenv("RATE_LIMIT_API_PER_MINUTE", "180")), 60)
        response = await call_next(request)
    except HTTPException as exc:
        emit_telemetry(
            logger, logging.WARNING, "request.rejected", request_id=request_id,
            method=request.method, status_code=exc.status_code,
            tenant_id=getattr(getattr(request.state, "tenant", None), "tenant_id", None),
            tenant_authorized=bool(getattr(request.state, "tenant", None)),
            status="error", outcome="rejected", error_code=telemetry_error_code(exc),
            dependency="api",
        )
        response = JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": safe_public_error(exc.detail), "request_id": request_id},
            headers=dict(exc.headers or {}),
        )
    except Exception as exc:
        # Do not use logger.exception here: tracebacks can contain provider
        # payloads, credentials, or request bodies. The request ID and class
        # code are sufficient to correlate the server-side failure safely.
        emit_telemetry(
            logger, logging.ERROR, "request.failed", request_id=request_id,
            method=request.method, status_code=500, status="error", outcome="failed",
            tenant_id=getattr(getattr(request.state, "tenant", None), "tenant_id", None),
            tenant_authorized=bool(getattr(request.state, "tenant", None)),
            error_code=telemetry_error_code(exc), dependency="api",
        )
        response = JSONResponse(
            status_code=500,
            content={"ok": False, "error": "Internal server error", "request_id": request_id},
        )
    _ms = (_time.perf_counter() - _t0) * 1000.0
    if _ms > float(os.getenv("SLOW_REQUEST_MS", "1500")):
        emit_telemetry(
            logger, logging.WARNING, "request.slow", request_id=request_id,
            method=request.method, status_code=response.status_code,
            tenant_id=getattr(getattr(request.state, "tenant", None), "tenant_id", None),
            tenant_authorized=bool(getattr(request.state, "tenant", None)),
            status="completed", latency_ms=_ms, dependency="api",
        )
    emit_telemetry(
        logger, logging.INFO, "request.completed", request_id=request_id,
        method=request.method, status_code=response.status_code,
        tenant_id=getattr(getattr(request.state, "tenant", None), "tenant_id", None),
        tenant_authorized=bool(getattr(request.state, "tenant", None)),
        status="success" if response.status_code < 400 else "error",
        outcome="success" if response.status_code < 400 else "failed",
        latency_ms=_ms, dependency="api",
    )
    response.headers["Server-Timing"] = f"app;dur={_ms:.1f}"
    response.headers["x-request-id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers.setdefault("Cache-Control", "no-store")
    if os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod", "staging"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "error": "Invalid request",
            "details": safe_validation_details(exc.errors()),
            "request_id": request_id,
        },
        headers={"x-request-id": request_id},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": safe_public_error(exc.detail), "request_id": request_id},
        headers={"x-request-id": request_id},
    )


@app.on_event("startup")
async def startup():
    # One sized pool for both worlds: sync route handlers (anyio limiter) and
    # asyncio.to_thread / aexec (the loop's default executor). Previously two
    # blocks set the limiter to 120 then overwrote it with 64, and to_thread
    # calls ran on asyncio's tiny default pool (min(32, cpu+4)).
    try:
        from app.services.aio import configure_executor
        _n = configure_executor()
        logger.info("io threadpool size=%d", _n)
    except Exception as _e:  # never block boot on tuning
        emit_telemetry(logger, logging.WARNING, "startup.executor_tuning_skipped", status="degraded", dependency="threadpool", outcome="optional_failure", error_code=telemetry_error_code(_e))

    env = os.getenv("ENVIRONMENT", "dev").lower()
    from app.core.runtime_config import validate_runtime_configuration
    validate_runtime_configuration(process="api")
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "UNSUBSCRIBE_SECRET", "INTEGRATION_OAUTH_STATE_SECRET", "REDIS_URL") if not os.getenv(k)]
    if not (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")):
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    if env in ("production", "prod"):
        required = {
            "LEAD_ENGINE_SERVICE_KEY": lambda v: len(v) >= 32,
            "DELIVERY_CONFIG_KEY": lambda v: len(v) >= 32,
            "STRIPE_SECRET_KEY": bool,
            "STRIPE_WEBHOOK_SECRET": bool,
            "CORS_ORIGINS": bool,
        }
        for key, predicate in required.items():
            value = os.getenv(key, "")
            if not predicate(value): missing.append(key)
        if os.getenv("LEAD_ENGINE_DEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes"):
            missing.append("LEAD_ENGINE_DEV_AUTH_BYPASS=false")
        if missing:
            raise RuntimeError("Production startup refused: missing/invalid required configuration: " + ", ".join(sorted(set(missing))))
    elif missing:
        logger.warning("Non-production missing env: %s", sorted(set(missing)))
    logger.info("Lead service started env=%s version=15.8.1", env)


@app.get("/health/routes")
def health_routes():
    paths=sorted({getattr(r,"path","") for r in app.routes if getattr(r,"path","")})
    expected={"leads":"/crm/{tenant_id}/leads","pipeline":"/crm/{tenant_id}/pipeline","dashboard":"/crm/{tenant_id}/dashboard","billing":"/billing/create-checkout-session","agents":"/crm/{tenant_id}/agents","email_verification":"/auth/email/send-code"}
    missing={k:v for k,v in expected.items() if v not in paths}
    return {"ok":not missing,"expected":expected,"missing":missing,"route_count":len(paths)}

def _health_request_id(request: Request) -> str:
    """Return the middleware correlation ID without trusting arbitrary input."""

    request_id = getattr(getattr(request, "state", None), "request_id", "")
    return request_id if re.fullmatch(r"[A-Za-z0-9._\\-]{8,64}", request_id or "") else uuid.uuid4().hex


def _health_component(*, ok: bool, required: bool, status: str, reason: str, **details) -> dict:
    """Build a stable, operator-safe component status object."""

    result = {"ok": bool(ok), "required": bool(required), "status": status, "reason": reason}
    result.update(details)
    return result


def _check_database() -> dict:
    configured = bool(
        os.getenv("SUPABASE_URL")
        and (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
             or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"))
    )
    if not configured:
        return _health_component(
            ok=False, required=True, status="unavailable", reason="missing_configuration"
        )
    try:
        from app.memory.supabase_client import get_supabase
        get_supabase().table("tenants").select("id").limit(1).execute()
        return _health_component(ok=True, required=True, status="ready", reason="ok")
    except Exception:
        logger.warning("health dependency failure component=db reason=dependency_unavailable")
        return _health_component(
            ok=False, required=True, status="unavailable", reason="dependency_unavailable"
        )


def _check_redis(policy) -> dict:
    required = bool(policy and policy.require_redis_for_api_ready)
    if not required:
        return _health_component(
            ok=True, required=False, status="not_required", reason="policy_optional"
        )
    redis_url = getattr(policy, "redis_url", None)
    if not redis_url:
        return _health_component(
            ok=False, required=True, status="unavailable", reason="missing_configuration"
        )
    try:
        import redis
        client = redis.Redis.from_url(
            redis_url, decode_responses=True, socket_connect_timeout=0.5,
            socket_timeout=0.8, health_check_interval=30,
        )
        client.ping()
        return _health_component(ok=True, required=True, status="ready", reason="ok")
    except Exception:
        logger.warning("health dependency failure component=redis reason=dependency_unavailable")
        return _health_component(
            ok=False, required=True, status="unavailable", reason="dependency_unavailable"
        )


def _safe_worker(row: object) -> dict:
    """Expose only aggregate worker evidence; never return arbitrary row data."""

    if not isinstance(row, dict):
        return {}
    return {
        "worker_id": str(row.get("worker_id"))[:128] if row.get("worker_id") is not None else None,
        "status": str(row.get("status") or "unknown")[:32],
        "capacity": max(0, int(row.get("capacity") or 0)),
        "active_jobs": max(0, int(row.get("active_jobs") or 0)),
        "last_seen_at": str(row.get("last_seen_at"))[:64] if row.get("last_seen_at") else None,
    }


def _check_worker_queue(*, required: bool) -> tuple[dict, dict]:
    if not required:
        component = _health_component(
            ok=True, required=False, status="not_required", reason="policy_optional"
        )
        return component, dict(component)
    try:
        from app.integrations.prod.scale_control import scale_health
        observed = scale_health() or {}
        raw_workers = observed.get("workers") if isinstance(observed, dict) else []
        workers = [_safe_worker(row) for row in (raw_workers or [])]
        workers = [row for row in workers if row]
        healthy_workers = [row for row in workers if row.get("status") in {"healthy", "ready"}]
        raw_queue = observed.get("queue") if isinstance(observed, dict) else {}
        raw_queue = raw_queue if isinstance(raw_queue, dict) else {}
        queued = max(0, int(raw_queue.get("queued") or 0))
        running = max(0, int(raw_queue.get("running") or 0))
        failed = max(0, int(raw_queue.get("failed") or 0))
        oldest_age = max(0, int(raw_queue.get("oldest_queued_seconds") or 0))
        worker_ok = bool(healthy_workers)
        queue_ok = bool(observed.get("ok")) and worker_ok
        worker = _health_component(
            ok=worker_ok, required=True,
            status="ready" if worker_ok else "unavailable",
            reason="ok" if worker_ok else "worker_unavailable",
            count=len(workers), healthy_count=len(healthy_workers), workers=workers,
        )
        queue = _health_component(
            ok=queue_ok, required=True,
            status="ready" if queue_ok else "unavailable",
            reason="ok" if queue_ok else "queue_unavailable",
            depth=queued + running, queued=queued, running=running, failed=failed,
            oldest_queued_seconds=oldest_age,
        )
        return worker, queue
    except Exception:
        logger.warning("health dependency failure component=worker_queue reason=dependency_unavailable")
        unavailable = _health_component(
            ok=False, required=True, status="unavailable", reason="dependency_unavailable"
        )
        return dict(unavailable), dict(unavailable)


def _health_route_contract() -> dict:
    paths = {getattr(route, "path", "") for route in app.routes}
    expected = {
        "leads": "/crm/{tenant_id}/leads",
        "pipeline": "/crm/{tenant_id}/pipeline",
        "dashboard": "/crm/{tenant_id}/dashboard",
        "billing": "/billing/create-checkout-session",
        "agents": "/crm/{tenant_id}/agents",
        "email_verification": "/auth/email/send-code",
    }
    missing = {name: path for name, path in expected.items() if path not in paths}
    return {"ok": not missing, "expected": expected, "missing": missing, "route_count": len(paths)}


def _health_snapshot(request: Request) -> tuple[dict, bool]:
    import time as _time

    started = _time.perf_counter()
    request_id = _health_request_id(request)
    try:
        from app.core.runtime_config import load_runtime_policy
        policy = load_runtime_policy(process="api")
        bootstrap = _health_component(ok=True, required=True, status="ready", reason="ok")
    except Exception:
        logger.warning("health dependency failure component=bootstrap reason=invalid_configuration")
        policy = None
        bootstrap = _health_component(
            ok=False, required=True, status="unavailable", reason="invalid_configuration"
        )

    database = _check_database()
    redis = _check_redis(policy)
    worker, queue = _check_worker_queue(required=bool(policy and policy.require_worker_ready))
    routes = _health_route_contract()
    route_component = _health_component(
        ok=bool(routes["ok"]), required=True,
        status="ready" if routes["ok"] else "unavailable",
        reason="ok" if routes["ok"] else "route_contract_missing",
        missing=routes["missing"],
    )
    components = {
        "db": database,
        "redis": redis,
        "worker": worker,
        "queue": queue,
        "bootstrap": bootstrap,
        "routes": route_component,
    }
    ready = all(component["ok"] for component in components.values() if component["required"])
    elapsed_ms = round((_time.perf_counter() - started) * 1000, 1)
    payload = {
        "ok": ready,
        "service": "lead-engine",
        "components": components,
        # Preserve the original readiness fields for existing callers.
        "db": database["ok"],
        "redis": redis["ok"],
        "worker": worker["ok"],
        "queue": queue["ok"],
        "ms": elapsed_ms,
        "request_id": request_id,
    }
    return payload, ready


@app.get("/health/live")
def health_live(request: Request):
    """Process-only liveness; dependency failures belong to readiness."""

    return {"ok": True, "service": "lead-engine", "request_id": _health_request_id(request)}


@app.get("/health")
def health(request: Request):
    payload, ready = _health_snapshot(request)
    route_paths = {getattr(route, "path", "") for route in app.routes}
    crm_routes = {
        "me": "/crm/me" in route_paths,
        "dashboard": "/crm/{tenant_id}/dashboard" in route_paths,
        "leads": "/crm/{tenant_id}/leads" in route_paths,
        "pipeline": "/crm/{tenant_id}/pipeline" in route_paths,
        "reports": "/crm/{tenant_id}/reports/summary" in route_paths,
        "deal_reports": "/crm/{tenant_id}/reports/deals" in route_paths,
        "support_ask": "/support/ask" in route_paths,
    }
    worker_details = payload["components"]["worker"]
    queue_details = payload["components"]["queue"]
    checks = {
        "api": True,
        "db": payload["db"],
        "service_key": bool(os.getenv("LEAD_ENGINE_SERVICE_KEY")),
        "crm_routes": crm_routes,
        "crm_route_contract_ok": all(crm_routes.values()),
        "dependencies": payload["components"],
        "scale": {
            "ok": payload["worker"] and payload["queue"],
            "workers": worker_details.get("workers", []),
            "worker_count": worker_details.get("count", 0),
            "queue": {
                "queued": queue_details.get("queued", 0),
                "running": queue_details.get("running", 0),
                "failed": queue_details.get("failed", 0),
                "oldest_queued_seconds": queue_details.get("oldest_queued_seconds", 0),
            },
            "worker": worker_details,
        },
    }
    return {
        "ok": bool(ready and checks["crm_route_contract_ok"]),
        "service": "lead-engine",
        "mode": "managed_service",
        "version": "15.8.1",
        "request_id": payload["request_id"],
        "checks": checks,
    }


@app.get("/")
async def root():
    return {
        "service": "Automiqo Lead Service",
        "docs": "/docs",
        "health": "/health",
        "operator_api": "/leads/v2",
        "crm_api": "/crm",
    }




# Runtime auth bootstrap endpoint. Keep this directly on the application as a
# hard guarantee for the client portal: even if an optional CRM router fails to
# import during startup, the workspace bootstrap endpoint remains registered.
# The same verified tenant resolver is used, so this does NOT weaken auth.
try:
    from app.middleware.auth import require_tenant, TenantContext

    @app.get("/crm/me", tags=["crm-portal-bootstrap"])
    async def runtime_crm_me(ctx: TenantContext = Depends(require_tenant)):
        return {"tenant_id": ctx.tenant_id, "role": ctx.role, "email": ctx.email}
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="runtime_bootstrap", outcome="module_unavailable", error_code=telemetry_error_code(e))



try:
    from app.api.prod.leads_api import router as leads_router
    app.include_router(leads_router)
    logger.info("Mounted /leads/v2")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="leads_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    # Single CRM router, JWT-protected via require_tenant (crm_api.py
    # docstring has the detail). crm_public_router carries /team/accept,
    # which needs a lighter auth check since accepting an invite happens
    # before someone has tenant membership yet.
    from app.crm.crm_api import router as crm_router, crm_public_router
    from app.crm.crm_api import crm_unsubscribe_preview, crm_unsubscribe
    app.include_router(crm_router)
    app.include_router(crm_public_router)
    # Emails sent before the link format was corrected point at the bare
    # /unsubscribe path; keep those links (and RFC 8058 one-click POSTs) working.
    app.add_api_route("/unsubscribe", crm_unsubscribe_preview, methods=["GET"], include_in_schema=False)
    app.add_api_route("/unsubscribe", crm_unsubscribe, methods=["POST"], include_in_schema=False)
    logger.info("Mounted /crm (require_tenant) + /crm public (team/accept)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="crm_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    # Was written but never mounted — every companies/contacts/deals/
    # activities/notes route was a 404. Mounted after crm_api so that
    # router keeps precedence on any shared prefix.
    from app.crm.crm_entities_api import router as crm_entities_router
    app.include_router(crm_entities_router)
    logger.info("Mounted /crm entities (companies/contacts/deals)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="crm_entities", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.crm.crm_extras_api import router as crm_extras_router, public_router as crm_extras_public
    app.include_router(crm_extras_router)
    app.include_router(crm_extras_public)
    logger.info("Mounted /crm extras (notifications/views/import/reports)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="crm_extras", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.ops_api import router as ops_router, machine_router as ops_machine_router
    app.include_router(ops_router)
    app.include_router(ops_machine_router)
    logger.info("Mounted /leads/v2 ops (jobs/notifications/prospecting)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="ops_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.intelligence_api import router as intelligence_router
    from app.api.prod.lead_intelligence_6_api import router as lead_intelligence_6_router
    app.include_router(intelligence_router)
    app.include_router(lead_intelligence_6_router)
    logger.info("Mounted /crm intelligence (AI briefs/revenue funnel/learning)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="intelligence_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.agents_api import router as agents_router
    app.include_router(agents_router)
    from app.api.prod.approval_api import router as approval_router
    app.include_router(approval_router)
    try:
        from app.api.prod.agent_missions_api import router as agent_missions_router
        app.include_router(agent_missions_router)
    except Exception as exc:
        emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="agent_missions", outcome="module_unavailable", error_code=telemetry_error_code(exc))
    try:
        from app.api.prod.fabric_api import router as fabric_router
        app.include_router(fabric_router)
    except Exception as exc:
        emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="fabric_api", outcome="module_unavailable", error_code=telemetry_error_code(exc))
    logger.info("Mounted /crm agents + missions + data fabric (LeadX 15.8)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="agents_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.billing_api import router as billing_router, public_router as billing_public_router
    app.include_router(billing_router)
    app.include_router(billing_public_router)
    logger.info("Mounted /billing (Stripe Checkout + webhook)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="billing_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.email_verification_api import router as email_verification_router
    app.include_router(email_verification_router)
    logger.info("Mounted /auth email verification")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="email_verification_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.support_api import client_router as support_client_router, admin_router as support_admin_router
    app.include_router(support_client_router)
    app.include_router(support_admin_router)
    logger.info("Mounted /support + /support/admin")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="support_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    from app.api.prod.upgrade_api import public_router as upgrade_public, router as upgrade_router, agency_router as upgrade_agency
    from app.api.prod.revenue_os_api import router as revenue_os_router
    app.include_router(upgrade_public)
    app.include_router(upgrade_router)
    app.include_router(upgrade_agency)
    app.include_router(revenue_os_router)
    logger.info("Mounted upgrade API (scoreboard, CRM sync, why-now, waterfall, agency)")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="upgrade_api", outcome="module_unavailable", error_code=telemetry_error_code(e))


# Required portal/control-plane routes are not optional. Historically several
# router imports were wrapped in broad try/except blocks, so an import/dependency
# problem could leave the server alive while silently returning 404s. Validate
# the complete contract once all routers have attempted to mount.
_REQUIRED_ROUTE_PATHS = {
    "/auth/email/send-code",
    "/billing/create-checkout-session",
    "/crm/{tenant_id}/agent-missions",
    "/crm/{tenant_id}/agents",
    "/crm/{tenant_id}/ai/brief",
    "/crm/{tenant_id}/companies",
    "/crm/{tenant_id}/contacts",
    "/crm/{tenant_id}/deals",
    "/crm/{tenant_id}/fabric/overview",
    "/support/ask",
}

def _validate_required_route_contract() -> None:
    mounted = {getattr(r, "path", "") for r in app.routes}
    missing = sorted(_REQUIRED_ROUTE_PATHS - mounted)
    if missing:
        raise RuntimeError(
            "Required LeadX routes failed to mount: " + ", ".join(missing)
            + ". Check API dependency/import errors in the startup log."
        )
    logger.info("Required route contract PASS (%d routes)", len(_REQUIRED_ROUTE_PATHS))

_validate_required_route_contract()

# Runtime safety nets for individual lead operations. These are deliberately
# installed only when the primary CRM router did not mount the corresponding
# route. This prevents an import/deployment mismatch from turning every lead
# click or pipeline drag into a misleading 404.
try:
    from uuid import UUID as _LeadUUID
    from fastapi import HTTPException as _LeadHTTPException
    from app.middleware.auth import require_tenant as _lead_require_tenant, TenantContext as _LeadTenantContext

    _crm_route_paths = {getattr(r, "path", "") for r in app.routes}

    if "/crm/{tenant_id}/leads/{assignment_id}" not in _crm_route_paths:
        @app.get("/crm/{tenant_id}/leads/{assignment_id}", tags=["crm-portal-runtime"])
        async def runtime_crm_lead_detail(
            tenant_id: _LeadUUID, assignment_id: _LeadUUID,
            ctx: _LeadTenantContext = Depends(_lead_require_tenant),
        ):
            if str(tenant_id) != ctx.tenant_id:
                raise _LeadHTTPException(403, "Tenant mismatch")
            from app.memory.supabase_client import get_supabase
            import asyncio as _lead_asyncio
            tid, aid = str(tenant_id), str(assignment_id)

            def _read():
                sb = get_supabase()
                rows = sb.table("lead_assignments").select("*").eq("tenant_id", tid).eq("id", aid).limit(1).execute().data or []
                if not rows:
                    raise _LeadHTTPException(404, "Lead not found")
                a = rows[0]
                lead_id = a.get("lead_id")
                g = {}
                if lead_id:
                    g_rows = sb.table("leads_global").select("*").eq("id", str(lead_id)).limit(1).execute().data or []
                    g = g_rows[0] if g_rows else {}
                e = {}
                if lead_id:
                    try:
                        e_rows = sb.table("enrichment").select("*").eq("lead_id", str(lead_id)).order("extracted_at", desc=True).limit(1).execute().data or []
                        e = e_rows[0] if e_rows else {}
                    except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="database", outcome="optional_failure", error_code=telemetry_error_code(exc))
                activities = sb.table("lead_activities").select("*").eq("tenant_id", tid).eq("assignment_id", aid).order("created_at", desc=True).limit(100).execute().data or []
                tasks = sb.table("lead_tasks").select("*").eq("tenant_id", tid).eq("assignment_id", aid).order("created_at", desc=True).limit(100).execute().data or []
                lead = {**g, **e, **a, "assignment_id": aid, "lead_id": lead_id, "tenant_id": tid,
                        "company_name": g.get("name") or g.get("company_name") or "Lead",
                        "name": g.get("name") or g.get("company_name") or "Lead",
                        "total_score": a.get("score") or 0, "tier": a.get("tier") or "C",
                        "stage_id": a.get("stage_id"), "stage_slug": a.get("stage_slug") or "new",
                        "assignment_status": a.get("status") or "active"}
                return {"lead": lead, "activities": activities, "tasks": tasks, "why_this_lead": None}
            return await _lead_asyncio.to_thread(_read)

        logger.warning("CRM lead detail route missing; installed protected runtime fallback")

    _crm_route_paths = {getattr(r, "path", "") for r in app.routes}
    if "/crm/{tenant_id}/leads/{assignment_id}/stage" not in _crm_route_paths:
        @app.post("/crm/{tenant_id}/leads/{assignment_id}/stage", tags=["crm-portal-runtime"])
        async def runtime_crm_change_stage(
            tenant_id: _LeadUUID, assignment_id: _LeadUUID,
            body: dict,
            ctx: _LeadTenantContext = Depends(_lead_require_tenant),
        ):
            if str(tenant_id) != ctx.tenant_id:
                raise _LeadHTTPException(403, "Tenant mismatch")
            from app.memory.supabase_client import get_supabase
            import asyncio as _stage_asyncio
            tid, aid, stage_id = str(tenant_id), str(assignment_id), str(body.get("stage_id") or "")
            if not stage_id:
                raise _LeadHTTPException(400, "stage_id is required")
            def _write():
                sb = get_supabase()
                current = sb.table("lead_assignments").select("lead_id,stage_id,status").eq("tenant_id", tid).eq("id", aid).limit(1).execute().data or []
                if not current:
                    raise _LeadHTTPException(404, "Lead not found")
                stages = sb.table("pipeline_stages").select("id,tenant_id,name,slug,color,position,is_won,is_lost").eq("tenant_id", tid).eq("id", stage_id).limit(1).execute().data or []
                if not stages:
                    raise _LeadHTTPException(404, "Pipeline stage not found")
                stage = stages[0]
                status = "won" if stage.get("is_won") else ("rejected" if stage.get("is_lost") else "new")
                if stage.get("slug") in ("contacted", "qualified", "proposal", "negotiation"):
                    status = "contacted"
                updated = sb.table("lead_assignments").update({"stage_id": stage_id, "stage_slug": stage.get("slug"), "status": status}).eq("tenant_id", tid).eq("id", aid).execute()
                if not getattr(updated, "data", None):
                    raise _LeadHTTPException(409, "Lead stage update was not persisted")
                return {"ok": True, "stage": stage, "assignment_id": aid}
            return await _stage_asyncio.to_thread(_write)
        logger.warning("CRM stage route missing; installed protected runtime fallback")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.fallback_install_failed", status="degraded", dependency="crm_fallback", outcome="module_unavailable", error_code=telemetry_error_code(e))

# Runtime safety net for the client home screen. The normal CRM router owns
# this path when mounted. If an optional CRM import ever fails, do not expose a
# 404 to the portal — keep the protected dashboard contract available.
try:
    from uuid import UUID as _UUID
    import asyncio as _asyncio
    from datetime import datetime as _datetime, timezone as _timezone, timedelta as _timedelta

    _dashboard_path_exists = any(getattr(r, "path", "") == "/crm/{tenant_id}/dashboard" for r in app.routes)
    if not _dashboard_path_exists:
        @app.get("/crm/{tenant_id}/dashboard", tags=["crm-portal-runtime"])
        async def runtime_crm_dashboard(tenant_id: _UUID, ctx: TenantContext = Depends(require_tenant)):
            if str(tenant_id) != ctx.tenant_id:
                from fastapi import HTTPException
                raise HTTPException(403, "Tenant mismatch")
            tid = str(tenant_id)
            since = (_datetime.now(_timezone.utc) - _timedelta(days=6)).date()
            period = _datetime.now(_timezone.utc).replace(day=1).date().isoformat()
            def _read():
                from app.memory.supabase_client import get_supabase
                sb = get_supabase()
                def q(table, columns="*", limit=None):
                    try:
                        x = sb.table(table).select(columns).eq("tenant_id", tid)
                        if limit: x = x.limit(limit)
                        return x.execute().data or []
                    except Exception:
                        return []
                stages = q("pipeline_stages", "id,name,slug,color,position,is_won,is_lost")
                cards = q("crm_lead_cards", "assignment_id,stage_slug,tier,total_score,assignment_status")
                if not cards:
                    assignments = q("lead_assignments", "id,stage_id,stage_slug,tier,score,status,created_at,delivered_at", 1000)
                    cards = [{"assignment_id": a.get("id"), "stage_id": a.get("stage_id"), "stage_slug": a.get("stage_slug") or "new", "tier": a.get("tier") or "C", "total_score": a.get("score") or 0, "assignment_status": a.get("status") or "active"} for a in assignments]
                if not stages:
                    defaults = [("New","new",0,"#94a3b8",False,False),("Contacted","contacted",1,"#3b82f6",False,False),("Qualified","qualified",2,"#8b5cf6",False,False),("Proposal","proposal",3,"#f59e0b",False,False),("Negotiation","negotiation",4,"#f97316",False,False),("Won","won",5,"#22c55e",True,False),("Lost","lost",6,"#ef4444",False,True)]
                    stages = [{"id":f"runtime-{slug}","name":name,"slug":slug,"color":color,"position":pos,"is_won":won,"is_lost":lost} for name,slug,pos,color,won,lost in defaults]
                tasks = q("lead_tasks", "id,title,due_at,priority,assignment_id", 10)
                activities = q("lead_activities", "id,activity_type,title,body,created_at,assignment_id", 15)
                usage = q("usage_log", "*", 1)
                tenant = q("tenants", "name,plan,leads_per_month_limit,delivery_channel", 1)
                try:
                    delivered = sb.table("lead_assignments").select("delivered_at").eq("tenant_id", tid).gte("delivered_at", since.isoformat()).execute().data or []
                except Exception:
                    delivered = []
                by_stage, by_tier = {}, {"A":0,"B":0,"C":0}
                for c in cards:
                    slug=c.get("stage_slug") or "new"; by_stage[slug]=by_stage.get(slug,0)+1
                    tier=c.get("tier") or "C"; by_tier[tier]=by_tier.get(tier,0)+1
                won, lost = by_stage.get("won",0), by_stage.get("lost",0)
                closed=won+lost
                trend=[]
                for i in range(7):
                    day=(since+_timedelta(days=i)).isoformat()
                    trend.append({"date":day,"count":sum(1 for r in delivered if str(r.get("delivered_at") or "")[:10]==day)})
                return {"tenant":tenant[0] if tenant else None,"kpis":{"total_leads":len(cards),"tier_a":by_tier.get("A",0),"tier_b":by_tier.get("B",0),"tier_c":by_tier.get("C",0),"new":by_stage.get("new",0),"in_pipeline":len(cards)-won-lost,"won":won,"lost":lost,"win_rate_pct":round(100*won/closed,1) if closed else 0},"funnel":[{"stage":x.get("name"),"slug":x.get("slug"),"color":x.get("color"),"count":by_stage.get(x.get("slug") or "",0)} for x in stages],"usage":usage[0] if usage else None,"quota_limit":tenant[0].get("leads_per_month_limit") if tenant else None,"tasks_due":tasks,"recent_activity":activities,"trend":trend}
            return await _asyncio.to_thread(_read)

        logger.warning("CRM router did not mount dashboard; installed protected runtime fallback /crm/{tenant_id}/dashboard")
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.fallback_install_failed", status="degraded", dependency="dashboard_fallback", outcome="module_unavailable", error_code=telemetry_error_code(e))

# Runtime safety nets for the two primary client work surfaces. If an optional
# CRM module fails to import during a deployment, keep the client contract
# alive instead of returning a misleading 404 from FastAPI.
try:
    from uuid import UUID as _UUID2
    import asyncio as _asyncio2
    import time as _time2
    from fastapi import HTTPException as _HTTPException2
    from app.middleware.auth import require_tenant as _require_tenant2, TenantContext as _TenantContext2

    _crm_paths = {getattr(r, "path", "") for r in app.routes}

    if "/crm/{tenant_id}/leads" not in _crm_paths:
        @app.get("/crm/{tenant_id}/leads", tags=["crm-portal-runtime"])
        async def runtime_crm_leads(tenant_id: _UUID2, limit: int = 50, offset: int = 0, stage: str | None = None, tier: str | None = None, search: str | None = None, ctx: _TenantContext2 = Depends(_require_tenant2)):
            if str(tenant_id) != ctx.tenant_id:
                raise _HTTPException2(403, "Tenant mismatch")
            tid = str(tenant_id)
            limit = max(1, min(int(limit), 200)); offset = max(0, int(offset))
            def _read():
                from app.memory.supabase_client import get_supabase
                sb = get_supabase()
                try:
                    q = sb.table("crm_lead_cards").select("*", count="exact").eq("tenant_id", tid)
                    if stage: q = q.eq("stage_slug", stage)
                    if tier: q = q.eq("tier", tier.upper())
                    if search: q = q.ilike("company_name", f"%{search}%")
                    r = q.order("delivered_at", desc=True).range(offset, offset + limit - 1).execute()
                    return {"total": r.count if getattr(r, "count", None) is not None else len(r.data or []), "limit": limit, "offset": offset, "leads": r.data or []}
                except Exception:
                    q = sb.table("lead_assignments").select("id,tenant_id,lead_id,status,tier,created_at,delivered_at,stage_id,stage_slug,icp_id").eq("tenant_id", tid)
                    if tier: q = q.eq("tier", tier.upper())
                    rows = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute().data or []
                    ids = [x.get("lead_id") for x in rows if x.get("lead_id")]
                    by_id = {}
                    if ids:
                        try: by_id = {x["id"]: x for x in (sb.table("leads_global").select("id,name,phone_normalized,email,website,city,state,address,category").in_("id", ids).execute().data or [])}
                        except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="database", outcome="optional_failure", error_code=telemetry_error_code(exc))
                    leads = [{"assignment_id": x.get("id"), "tenant_id": tid, "lead_id": x.get("lead_id"), "company_name": (by_id.get(x.get("lead_id")) or {}).get("name") or "Unknown", "phone": (by_id.get(x.get("lead_id")) or {}).get("phone") or (by_id.get(x.get("lead_id")) or {}).get("phone_normalized"), "email": (by_id.get(x.get("lead_id")) or {}).get("email"), "website": (by_id.get(x.get("lead_id")) or {}).get("website"), "city": (by_id.get(x.get("lead_id")) or {}).get("city"), "state": (by_id.get(x.get("lead_id")) or {}).get("state"), "tier": x.get("tier"), "total_score": x.get("score"), "stage_slug": x.get("stage_slug") or "new", "assignment_status": x.get("status") or "active", "delivered_at": x.get("delivered_at") or x.get("created_at")} for x in rows]
                    return {"total": len(leads), "limit": limit, "offset": offset, "leads": leads}
            return await _asyncio2.to_thread(_read)

    if "/crm/{tenant_id}/pipeline" not in _crm_paths:
        @app.get("/crm/{tenant_id}/pipeline", tags=["crm-portal-runtime"])
        async def runtime_crm_pipeline(tenant_id: _UUID2, ctx: _TenantContext2 = Depends(_require_tenant2)):
            if str(tenant_id) != ctx.tenant_id:
                raise _HTTPException2(403, "Tenant mismatch")
            tid = str(tenant_id)
            def _read():
                from app.memory.supabase_client import get_supabase
                sb = get_supabase()
                stages = sb.table("pipeline_stages").select("id,tenant_id,name,slug,color,position,is_won,is_lost").eq("tenant_id", tid).order("position").execute().data or []
                try:
                    cards = sb.table("crm_lead_cards").select("*").eq("tenant_id", tid).order("total_score", desc=True).execute().data or []
                except Exception:
                    cards = []
                if not cards:
                    try:
                        assignments = (sb.table("lead_assignments")
                            .select("id,lead_id,stage_id,stage_slug,tier,status,created_at,delivered_at")
                            .eq("tenant_id", tid).order("created_at", desc=True).limit(1000).execute().data or [])
                        ids = [a.get("lead_id") for a in assignments if a.get("lead_id")]
                        by_lead = {}
                        if ids:
                            try:
                                gl = (sb.table("leads_global").select("id,name,phone_normalized,phone_e164,email,city,state,website")
                                      .in_("id", ids).execute().data or [])
                                by_lead = {str(x.get("id")): x for x in gl}
                            except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="database", outcome="optional_failure", error_code=telemetry_error_code(exc))
                        cards = [{
                            "assignment_id": a.get("id"), "lead_id": a.get("lead_id"),
                            "stage_id": a.get("stage_id"), "stage_slug": a.get("stage_slug") or "new",
                            "tier": a.get("tier") or "C", "total_score": a.get("score") or 0,
                            "assignment_status": a.get("status") or "active",
                            "company_name": (by_lead.get(str(a.get("lead_id"))) or {}).get("name") or "Lead",
                            "phone_normalized": (by_lead.get(str(a.get("lead_id"))) or {}).get("phone_normalized"),
                            "phone_e164": (by_lead.get(str(a.get("lead_id"))) or {}).get("phone_e164"),
                            "email": (by_lead.get(str(a.get("lead_id"))) or {}).get("email"),
                            "city": (by_lead.get(str(a.get("lead_id"))) or {}).get("city"),
                            "state": (by_lead.get(str(a.get("lead_id"))) or {}).get("state"),
                            "website": (by_lead.get(str(a.get("lead_id"))) or {}).get("website"),
                        } for a in assignments]
                    except Exception:
                        cards = []
                if not stages:
                    defaults = [("New","new",0,"#94a3b8",False,False),("Contacted","contacted",1,"#3b82f6",False,False),("Qualified","qualified",2,"#8b5cf6",False,False),("Proposal","proposal",3,"#f59e0b",False,False),("Negotiation","negotiation",4,"#f97316",False,False),("Won","won",5,"#22c55e",True,False),("Lost","lost",6,"#ef4444",False,True)]
                    stages = [{"id":f"runtime-{slug}","tenant_id":tid,"name":name,"slug":slug,"color":color,"position":pos,"is_won":won,"is_lost":lost} for name,slug,pos,color,won,lost in defaults]
                by_stage = {x.get("id"): [] for x in stages if x.get("id")}; by_slug = {x.get("slug"): x.get("id") for x in stages if x.get("slug")}; unstaged = []
                for card in cards:
                    target = card.get("stage_id") if card.get("stage_id") in by_stage else by_slug.get(card.get("stage_slug") or "new")
                    if target in by_stage: by_stage[target].append(card)
                    else: unstaged.append(card)
                return {"stages": [{**x, "leads": by_stage.get(x.get("id"), [])} for x in stages], "unstaged": unstaged, "needs_seed": False}
            return await _asyncio2.to_thread(_read)

    # Reports are part of the client CRM route contract. These fallbacks keep
    # a stale/partially mounted Codespaces backend from turning Reports into
    # a misleading 404. They are tenant-scoped and intentionally defensive.
    if "/crm/{tenant_id}/reports/summary" not in _crm_paths:
        @app.get("/crm/{tenant_id}/reports/summary", tags=["crm-portal-runtime"])
        async def runtime_crm_reports_summary(tenant_id: _UUID2, ctx: _TenantContext2 = Depends(_require_tenant2)):
            if str(tenant_id) != ctx.tenant_id:
                raise _HTTPException2(403, "Tenant mismatch")
            tid = str(tenant_id)
            def _read():
                from app.memory.supabase_client import get_supabase
                sb = get_supabase()
                cards = []
                try:
                    cards = (sb.table("crm_lead_cards")
                             .select("assignment_id,lead_id,tier,stage_slug,total_score,email")
                             .eq("tenant_id", tid).limit(1000).execute().data or [])
                except Exception:
                    cards = []
                if not cards:
                    try:
                        asg = (sb.table("lead_assignments")
                               .select("id,lead_id,tier,stage_slug")
                               .eq("tenant_id", tid).limit(1000).execute().data or [])
                        ids = [str(a.get("lead_id")) for a in asg if a.get("lead_id")]
                        by_lead = {}
                        if ids:
                            try:
                                gl = (sb.table("leads_global").select("id,email")
                                      .in_("id", ids).execute().data or [])
                                by_lead = {str(x.get("id")): x for x in gl}
                            except Exception as exc: emit_telemetry(logger, logging.DEBUG, "optional.read_failed", status="degraded", dependency="database", outcome="optional_failure", error_code=telemetry_error_code(exc))
                        cards = [{
                            "assignment_id": a.get("id"), "lead_id": a.get("lead_id"),
                            "tier": a.get("tier"), "stage_slug": a.get("stage_slug") or "new",
                            "total_score": a.get("score"),
                            "email": (by_lead.get(str(a.get("lead_id"))) or {}).get("email"),
                        } for a in asg]
                    except Exception:
                        cards = []
                total = len(cards)
                with_email = sum(1 for c in cards if c.get("email"))
                by_tier = {"A": 0, "B": 0, "C": 0}
                by_stage = {}
                for c in cards:
                    tier = str(c.get("tier") or "C").upper()
                    if tier not in by_tier:
                        tier = "C"
                    by_tier[tier] += 1
                    stage = c.get("stage_slug") or "new"
                    by_stage[stage] = by_stage.get(stage, 0) + 1
                scores = [float(c.get("total_score") or 0) for c in cards]
                return {
                    "total": total,
                    "with_email": with_email,
                    "with_email_pct": round(100 * with_email / total, 1) if total else 0,
                    "with_booking": 0,
                    "by_tier": by_tier,
                    "by_stage": by_stage,
                    "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
                }
            return await _asyncio2.to_thread(_read)

    if "/crm/{tenant_id}/reports/deals" not in _crm_paths:
        @app.get("/crm/{tenant_id}/reports/deals", tags=["crm-portal-runtime"])
        async def runtime_crm_deal_reports(tenant_id: _UUID2, days: int = 365, ctx: _TenantContext2 = Depends(_require_tenant2)):
            if str(tenant_id) != ctx.tenant_id:
                raise _HTTPException2(403, "Tenant mismatch")
            days = max(7, min(int(days), 1095))
            tid = str(tenant_id)
            try:
                from app.integrations.prod.crm_reports import deal_report as _deal_report
                return await _asyncio2.to_thread(_deal_report, tid, days)
            except Exception as report_error:
                emit_telemetry(logger, logging.WARNING, "crm.deal_report_fallback", status="degraded", dependency="deal_report", outcome="fallback", error_code=telemetry_error_code(report_error))
                return {
                    "ok": True, "window_days": days, "revenue_won": 0, "deals_won": 0,
                    "avg_deal_value": 0, "win_rate": 0, "avg_cycle_days": None,
                    "open_deals": 0, "open_value": 0, "forecast_weighted": 0,
                    "revenue_from_delivered_leads": 0, "attribution_pct": 0,
                    "by_stage": [], "monthly_revenue": [], "currency": "USD",
                    "degraded": True,
                }

    _final_paths = {getattr(r, "path", "") for r in app.routes}
    logger.info(
        "CRM route contract: leads=%s pipeline=%s dashboard=%s reports=%s deal_reports=%s",
        "/crm/{tenant_id}/leads" in _final_paths,
        "/crm/{tenant_id}/pipeline" in _final_paths,
        "/crm/{tenant_id}/dashboard" in _final_paths,
        "/crm/{tenant_id}/reports/summary" in _final_paths,
        "/crm/{tenant_id}/reports/deals" in _final_paths,
    )

except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.module_mount_failed", status="degraded", dependency="platform_api", outcome="module_unavailable", error_code=telemetry_error_code(e))

try:
    # Import for the side effect of populating jobs.HANDLERS, so an API
    # process can validate a job kind before enqueuing it.
    # NOTE: must be `from ... import`, NOT `import app.integrations...` —
    # a dotted import would rebind the name `app` here to the package
    # module, clobbering the FastAPI instance above.
    from app.integrations.prod import job_handlers  # noqa: F401
except Exception as e:
    emit_telemetry(logger, logging.ERROR, "startup.handlers_register_failed", status="degraded", dependency="job_handlers", outcome="module_unavailable", error_code=telemetry_error_code(e))


@app.get("/health/ready")
def health_ready(request: Request):
    """Dependency readiness with a truthful HTTP status and safe diagnostics."""

    payload, ready = _health_snapshot(request)
    if not ready:
        return JSONResponse(status_code=503, content=payload)
    return payload
