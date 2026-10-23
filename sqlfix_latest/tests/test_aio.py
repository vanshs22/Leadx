import asyncio
import os
import threading
import time

import pytest  # noqa: F401  (plain asserts; pytest only as runner)

from app.services import aio


class SlowBuilder:
    """Stands in for a postgrest builder: .execute() blocks like a network call."""

    def __init__(self, delay=0.3):
        self.delay, self.thread = delay, None

    def execute(self):
        self.thread = threading.get_ident()
        time.sleep(self.delay)
        return type("R", (), {"data": [1, 2, 3]})()


def test_aexec_does_not_block_event_loop():
    async def main():
        ticks = 0
        stop = False

        async def heartbeat():
            nonlocal ticks
            while not stop:
                await asyncio.sleep(0.03)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        b = SlowBuilder(0.3)
        res = await aio.aexec(b)
        stop = True
        await hb
        return res, ticks, b.thread

    res, ticks, thread = asyncio.run(main())
    assert res.data == [1, 2, 3]
    assert thread != threading.get_ident()          # ran in a worker thread
    assert ticks >= 5, f"event loop starved (ticks={ticks})"  # blocking call => ~0-1


def test_concurrent_queries_overlap():
    async def main():
        t0 = time.perf_counter()
        await asyncio.gather(*(aio.aexec(SlowBuilder(0.25)) for _ in range(6)))
        return time.perf_counter() - t0

    assert asyncio.run(main()) < 0.25 * 6 * 0.6  # serial would be 1.5s


def test_to_async_keeps_await_semantics_and_errors():
    @aio.to_async
    def add(a, b=1):
        return a + b

    @aio.to_async
    def boom():
        raise ValueError("x")

    assert add.__name__ == "add" and asyncio.iscoroutinefunction(add)
    assert asyncio.run(add(2, b=3)) == 5
    assert add.__sync__(1) == 2
    with pytest.raises(ValueError):
        asyncio.run(boom())


def test_gather_limited_respects_limit_and_order():
    running = peak = 0

    async def job(i):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return i

    out = asyncio.run(aio.gather_limited([job(i) for i in range(12)], limit=3))
    assert out == list(range(12)) and peak == 3


def test_gather_limited_returns_exceptions_not_raises():
    async def ok():
        return 1

    async def bad():
        raise RuntimeError("no")

    out = asyncio.run(aio.gather_limited([ok(), bad(), ok()], limit=2))
    assert out[0] == 1 and isinstance(out[1], RuntimeError) and out[2] == 1


def test_pool_size_takes_larger_value_never_silently_shrinks(monkeypatch=None):
    # regression: startup set 120 then overwrote it with 64
    old = {k: os.environ.get(k) for k in ("THREADPOOL_SIZE", "SYNC_DB_THREAD_TOKENS")}
    try:
        os.environ["THREADPOOL_SIZE"], os.environ["SYNC_DB_THREAD_TOKENS"] = "120", "64"
        assert aio.pool_size() == 120
        os.environ["THREADPOOL_SIZE"], os.environ["SYNC_DB_THREAD_TOKENS"] = "32", "64"
        assert aio.pool_size() == 64
        os.environ.pop("THREADPOOL_SIZE"); os.environ.pop("SYNC_DB_THREAD_TOKENS")
        assert aio.pool_size() == 120
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


def test_configure_executor_sets_default_pool():
    async def main():
        aio._EXECUTOR = None
        n = aio.configure_executor()
        return n, asyncio.get_running_loop()._default_executor._max_workers

    n, workers = asyncio.run(main())
    assert workers == n
