"""Shared timeout/retry policy for external providers."""
from __future__ import annotations
import asyncio, random
from typing import Awaitable, Callable, TypeVar
T=TypeVar("T")

async def retry(fn: Callable[[], Awaitable[T]], *, attempts: int=3, timeout_s: float=30, base_delay: float=.4) -> T:
    last: Exception | None = None
    for n in range(max(1, attempts)):
        try:
            return await asyncio.wait_for(fn(), timeout=timeout_s)
        except Exception as exc:
            last=exc
            if n == attempts-1: break
            await asyncio.sleep(base_delay*(2**n)+random.random()*.15)
    assert last is not None
    raise last
