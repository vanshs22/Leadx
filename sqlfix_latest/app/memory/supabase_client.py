"""
Supabase client for Lead Engine (service role).
Standalone — does not depend on Automiqo OS host.
"""
from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger("lead_engine.supabase")

_client: Any = None

# Modern server secrets are opaque API keys, but supabase-py validates the
# factory key as a JWT before the client can be reconfigured. This value is
# used only during local client construction and is never kept in client state.
_SYNTHETIC_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJzdXBhYmFzZS1jbGllbnQtcGxhY2Vob2xkZXIifQ."
    "synthetic-signature"
)


def _set_modern_secret_headers(client: Any, key: str) -> None:
    """Replace all factory-derived auth headers with the opaque API key."""
    # Auth callbacks use this value when they rebuild Authorization headers.
    client.supabase_key = key

    targets = [
        getattr(client, "options", None),
        getattr(client, "auth", None),
        getattr(getattr(client, "auth", None), "admin", None),
    ]
    postgrest = getattr(client, "_postgrest", None)
    if postgrest is not None:
        targets.extend(
            [
                postgrest,
                getattr(postgrest, "session", None),
            ]
        )

    for target in targets:
        headers = target if isinstance(target, dict) else getattr(target, "_headers", None)
        if headers is None and target is not None:
            headers = getattr(target, "headers", None)
        if headers is None:
            continue
        for name in ("Authorization", "authorization", "apiKey", "apikey"):
            headers.pop(name, None)
        headers["apikey"] = key


@lru_cache(maxsize=1)
def get_supabase():
    """
    Returns a supabase-py client using the service role key.
    Required env: SUPABASE_URL, SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY).
    """
    global _client
    if _client is not None:
        return _client

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = (
        os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
        or ""
    )
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY) must be set"
        )

    try:
        from supabase import create_client
    except ImportError as e:
        raise RuntimeError("pip install supabase") from e

    if key.startswith("sb_secret_"):
        # supabase-py validates the factory key as a JWT, while modern server
        # secrets are opaque API keys and must not become Bearer tokens.
        from supabase import ClientOptions

        options = ClientOptions(headers={"Authorization": ""})
        _client = create_client(url, _SYNTHETIC_JWT, options=options)
        _set_modern_secret_headers(_client, key)
    else:
        _client = create_client(url, key)

    logger.info("Supabase client initialized host=%s", url.split("//")[-1][:40])
    return _client


def reset_supabase_client() -> None:
    """For tests."""
    global _client
    _client = None
    clear_cache = getattr(get_supabase, "cache_clear", None)
    if callable(clear_cache):
        clear_cache()
