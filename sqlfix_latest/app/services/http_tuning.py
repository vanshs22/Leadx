"""Process-wide HTTP client tuning.

httpx builds and loads a CA bundle into a new SSLContext for every client it creates (about 40 ms of CPU). Several code paths
create a client per request, and when that happens on the event loop it stalls every other task. Caching the verified context
makes client creation cheap. Safe: the context is read-only after construction and is only used for the default verify=True case.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("lead_engine.http_tuning")
_installed = False


def install() -> bool:
    global _installed
    if _installed:
        return True
    try:
        import httpx._config as cfg
        original = cfg.SSLConfig.load_ssl_context_verify
        cache: dict[bool, object] = {}

        def cached(self):
            if self.verify is True and not self.cert:
                key = bool(getattr(self, "http2", False))
                if key not in cache:
                    cache[key] = original(self)
                return cache[key]
            return original(self)

        cfg.SSLConfig.load_ssl_context_verify = cached
        _installed = True
    except Exception as exc:  # a different httpx layout must never stop the app
        logger.debug("ssl context cache not installed: %s", type(exc).__name__)
    return _installed
