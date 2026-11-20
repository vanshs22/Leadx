"""Buffered, best-effort persistence of fetch attempts and per-lead acquisition outcomes (tables fetch_attempts, fetch_outcomes).

The dashboard (engine_api /engine/fetching) reads these tables. Writing never blocks or fails enrichment: a missing table or
database error drops the batch and logs once.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("lead_engine.fetch_telemetry")

# set by the enrichment layer around each lead: tenant_id, run_id, lead_key, discovery_source
ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("fetch_ctx", default={})
_lock = threading.Lock()
_attempts: list[dict] = []
_outcomes: list[dict] = []
FLUSH_AT = 200
counters: dict[str, int] = {}   # in-process event counts (e.g. discovery_dead_domain, firecrawl_calls); read by tests and the audit script


def count(name: str, n: int = 1) -> None:
    with _lock:
        counters[name] = counters.get(name, 0) + n


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_attempts(atts: list[dict]) -> None:
    c = ctx.get()
    rows = [{"tenant_id": c.get("tenant_id") or None, "run_id": c.get("run_id") or None, "lead_key": c.get("lead_key"), "discovery_source": c.get("discovery_source"),
             "domain": a["domain"], "url": a["url"], "provider": a["provider"], "category": a["category"], "kind": a["kind"], "http_status": a["status"],
             "ok": bool(a["ok"]), "elapsed_ms": a["ms"], "bytes": a["bytes"], "cost": a["cost"], "error": a["error"], "trigger": a["trigger"], "created_at": _now()} for a in atts]
    with _lock:
        _attempts.extend(rows)
        big = len(_attempts) >= FLUSH_AT
    if big:
        _spawn_flush()


def record_outcome(row: dict[str, Any]) -> None:
    c = ctx.get()
    row = {"tenant_id": c.get("tenant_id") or None, "run_id": c.get("run_id") or None, "lead_key": c.get("lead_key"), "discovery_source": c.get("discovery_source"),
           "created_at": _now(), **row}
    with _lock:
        _outcomes.append(row)


def _spawn_flush() -> None:
    try:
        import asyncio
        asyncio.get_running_loop().run_in_executor(None, flush)
    except RuntimeError:
        flush()


def flush() -> int:
    """Write buffered rows. Returns the number written."""
    with _lock:
        a, o = _attempts[:], _outcomes[:]
        _attempts.clear(); _outcomes.clear()
    n = 0
    try:
        from app.memory.supabase_client import get_supabase
        sb = get_supabase()
        for table, rows in (("fetch_attempts", a), ("fetch_outcomes", o)):
            for i in range(0, len(rows), 200):
                sb.table(table).insert(rows[i:i + 200]).execute()
                n += len(rows[i:i + 200])
    except Exception as exc:
        logger.warning("fetch telemetry dropped %s rows: %s", len(a) + len(o) - n, type(exc).__name__)
    return n


def seed_history(sb, days: int = 14, limit: int = 20000) -> int:
    """Warm the engine's provider/domain history and domain cooldowns from stored attempts, so a fresh worker starts informed."""
    from datetime import timedelta
    from app.integrations.prod.fetch_engine import Domain, get_engine
    import time
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = sb.table("fetch_attempts").select("domain,provider,category,trigger,kind,ok,elapsed_ms,created_at").gte("created_at", since).order("created_at", desc=True).limit(limit).execute().data or []
    rows.reverse()   # oldest first, so an operator release later in the log cancels an earlier cooldown
    eng = get_engine()
    for r in rows:
        if r["provider"] == "operator":
            if r["kind"] == "released":
                eng.domains.pop(r["domain"], None)
            continue
        eng.history.add(r["provider"], r["domain"], r.get("trigger") or "first", bool(r["ok"]), float(r.get("elapsed_ms") or 0))
        if not r["ok"] and r["provider"] == "direct" and r.get("kind") in ("challenge", "rate_limit"):
            try:
                age = time.time() - datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            from app.integrations.prod.fetch_classify import Verdict  # noqa: F401
            left = (21600 if r["kind"] == "challenge" else 900) - age
            if left > 0:
                eng.domains[r["domain"]] = Domain(time.time() + left, r["category"], r["kind"], r["category"], time.time() - age)
    return len(rows)


_seeded = False


def seed_once() -> None:
    """Warm engine history from the database the first time enrichment runs in this process. Never raises."""
    global _seeded
    if _seeded:
        return
    _seeded = True
    try:
        from app.memory.supabase_client import get_supabase
        seed_history(get_supabase())
    except Exception as exc:
        logger.debug("history seed skipped: %s", type(exc).__name__)
