"""Async bridge for the synchronous Supabase client and other blocking libs.

Why this exists
---------------
supabase-py is synchronous. Calling ``.execute()`` inside an ``async def`` runs
the network round-trip ON the event loop, so one slow query stalls every other
request on that worker. Everything here moves the blocking call to a worker
thread while keeping the caller's ``await`` semantics.

    rows = (await aexec(sb.table("leads_global").select("*").eq("id", i))).data
    result = await offload(sync_helper, arg, kw=1)

    @to_async                      # sync body, awaitable from async callers
    def list_members(tenant_id): ...

Pool sizing: ``asyncio.to_thread`` uses the *event loop's default executor*,
which is NOT the anyio limiter FastAPI uses for sync route handlers. Call
``configure_executor()`` once at startup (API and worker both do) so the two
pools are sized deliberately instead of silently defaulting to min(32, cpu+4).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Iterable, TypeVar

logger = logging.getLogger("lead_engine.aio")

T = TypeVar("T")

_EXECUTOR: ThreadPoolExecutor | None = None


def pool_size() -> int:
    """One knob for both pools. The larger of the two legacy env vars wins, so
    a smaller value can never silently overwrite a larger one."""
    vals = []
    for k in ("THREADPOOL_SIZE", "SYNC_DB_THREAD_TOKENS"):
        try:
            vals.append(int(os.getenv(k, "")))
        except ValueError:
            pass
    return max(vals) if vals else 120


def configure_executor(loop: asyncio.AbstractEventLoop | None = None) -> int:
    """Install a sized default executor on the running loop and raise anyio's
    limiter to match. Idempotent; never raises (tuning must not block boot)."""
    global _EXECUTOR
    n = pool_size()
    try:
        loop = loop or asyncio.get_running_loop()
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=n, thread_name_prefix="leadx-io")
        loop.set_default_executor(_EXECUTOR)
    except Exception as e:  # pragma: no cover
        logger.warning("default executor tuning skipped: %s", e)
    try:
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = n
    except Exception as e:  # anyio absent or no running loop
        logger.debug("anyio limiter tuning skipped: %s", e)
    return n


async def offload(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking callable in a worker thread (contextvars are copied)."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def aexec(builder: Any) -> Any:
    """``await aexec(sb.table(..).select(..))`` == ``builder.execute()`` off-loop."""
    return await asyncio.to_thread(builder.execute)


def to_async(fn: Callable[..., T]) -> Callable[..., Awaitable[T]]:
    """Expose a blocking function as a coroutine function.

    Drop-in for ``async def f`` bodies that never awaited anything: call sites
    keep ``await f(...)``; the body now runs in a worker thread."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(fn, *args, **kwargs)

    wrapper.__sync__ = fn  # type: ignore[attr-defined]  # for tests / sync callers
    return wrapper


async def gather_limited(
    coros: Iterable[Awaitable[T]], limit: int = 8, *, return_exceptions: bool = True
) -> list[Any]:
    """``asyncio.gather`` with a concurrency cap. Result order matches input."""
    sem = asyncio.Semaphore(max(1, limit))

    async def _run(c: Awaitable[T]) -> T:
        async with sem:
            return await c

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=return_exceptions)
