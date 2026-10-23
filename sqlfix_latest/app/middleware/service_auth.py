"""
Service-key auth for operator endpoints.

Simple testing mode (default for non-production):
  No key required when ENVIRONMENT is not production/staging,
  or LEAD_ENGINE_DEV_AUTH_BYPASS=1.

Production:
  Set ENVIRONMENT=production and LEAD_ENGINE_SERVICE_KEY (16+ chars).
  Clients must send Authorization: Bearer <key> or X-Service-Key.
"""
from __future__ import annotations

import hmac
import logging
import os
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

SERVICE_KEY = os.getenv("LEAD_ENGINE_SERVICE_KEY", "")


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in (
        "production",
        "prod",
        "staging",
    )


def _open_mode() -> bool:
    """Allow unauthenticated operator calls for local / Codespace testing.

    The environment check comes FIRST and is absolute: LEAD_ENGINE_DEV_AUTH_BYPASS
    cannot open the operator API in production/staging. It used to be able to,
    which meant a stray flag in a deployed .env exposed every operator endpoint
    while the tenant API (middleware/auth.py) correctly refused — same flag,
    two different answers. Mirrors ALLOW_DEV_BYPASS in middleware/auth.py.
    """
    bypass_requested = os.getenv("LEAD_ENGINE_DEV_AUTH_BYPASS", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if _is_production():
        if bypass_requested:
            logger.critical(
                "LEAD_ENGINE_DEV_AUTH_BYPASS is set but ENVIRONMENT is production/"
                "staging — operator-key bypass FORCE-DISABLED. Remove the flag."
            )
        return False
    return True  # development / local / empty → simple open testing


def _service_key() -> str:
    # Read at call time, not import time: app_service loads .env during startup,
    # and a module-level snapshot can be captured before that happens.
    return os.getenv("LEAD_ENGINE_SERVICE_KEY", "")


def _configured() -> bool:
    return len(_service_key()) >= 16


async def require_service_key(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if _open_mode():
        return

    if not _configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Set LEAD_ENGINE_SERVICE_KEY (min 16 chars) for production",
        )

    provided = None
    if creds and creds.credentials:
        provided = creds.credentials
    if not provided:
        provided = request.headers.get("X-Service-Key")

    if not provided or not hmac.compare_digest(provided, _service_key()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or missing service key",
            headers={"WWW-Authenticate": "Bearer"},
        )
