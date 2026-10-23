"""Durable-job contract with an in-process fallback.

Production deployments can point this abstraction at the existing Supabase jobs
queue/worker without changing layer code. HTTP handlers should enqueue long work
rather than hold requests open.
"""
from __future__ import annotations
import asyncio, inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

@dataclass(slots=True)
class Job:
    id: str
    kind: str
    tenant_id: str
    status: str = "queued"
    progress: int = 0
    current_stage: str = "queued"
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

_jobs: dict[str, Job] = {}

async def enqueue(kind: str, tenant_id: str, fn: Callable[[], Awaitable[dict[str, Any]]]) -> Job:
    job = Job(uuid4().hex, kind, tenant_id)
    _jobs[job.id] = job
    async def runner() -> None:
        job.status = "running"; job.current_stage = kind
        try:
            job.result = await fn()
            job.progress = 100; job.status = "completed"
        except Exception as exc:
            job.error = str(exc)[:1000]; job.status = "failed"
    asyncio.create_task(runner())
    return job

def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)
