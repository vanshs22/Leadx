"""Runtime controls for the paid enrichment waterfall.

* ordering   providers are ranked by learned hit rate per credit (ProviderIntelligence), falling back to priors
* misses     a provider that returned nothing for (lead, field) is not asked again until the field's TTL passes
* budgets    paid credits are capped per run (LEADX_RUN_CREDIT_CAP) and per tenant run (lead_policy.run_credit_cap)
* ledger     every paid attempt is written to enrichment_spend_ledger, the source for cost per delivered lead

Persistence is best-effort: a missing table or DB error degrades to in-process behaviour and never blocks enrichment.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.integrations.prod.provider_intelligence import ProviderIntelligence, load_global_snapshot, persist_global_snapshot

logger = logging.getLogger("lead_engine.waterfall_runtime")

FIELD_TTL_DAYS = {"email": 30, "phone": 90, "owner_name": 120, "website": 180, "linkedin_url": 180, "address": 365}
DEFAULT_TTL_DAYS = 60
_PERSIST_EVERY = 25

_intel: Optional[ProviderIntelligence] = None
_records_since_persist = 0
_misses: dict[tuple[str, str, str], float] = {}
_loaded_leads: set[tuple[str, str]] = set()
_spent: dict[str, float] = {}


def reset_state() -> None:
    global _intel, _records_since_persist
    _intel, _records_since_persist = None, 0
    _misses.clear(); _loaded_leads.clear(); _spent.clear()


def field_ttl_days(field: str) -> int:
    try:
        override = json.loads(os.getenv("LEADX_FIELD_TTL_JSON") or "{}")
    except Exception:
        override = {}
    return int(override.get(field) or FIELD_TTL_DAYS.get(field, DEFAULT_TTL_DAYS))


def lead_key(lead: dict) -> str:
    return str(lead.get("id") or lead.get("fingerprint") or lead.get("website_domain") or "")


def _sb():
    from app.memory.supabase_client import get_supabase
    return get_supabase()


# ── ordering ────────────────────────────────────────────────────────────────
def intel() -> ProviderIntelligence:
    global _intel
    if _intel is None:
        _intel = ProviderIntelligence(load_global_snapshot())
    return _intel


def order_providers(candidates: list[dict], field: str) -> list[dict]:
    """candidates: [{"name", "cost", "prior"}]. Highest expected hits per credit first; cost and name break ties."""
    pi = intel()

    def key(c: dict):
        cost = max(float(c.get("cost") or 0), 0.01)
        hit = pi.score(c["name"], field, base_prior=float(c.get("prior", 0.3)), cost=cost)
        return (-(hit / cost), cost, c["name"])
    return sorted(candidates, key=key)


def record_outcome(provider: str, field: str, *, hit: bool, cost: float, latency_ms: int, failed: bool = False,
                   region: str = "global", vertical: str = "global") -> None:
    global _records_since_persist
    intel().record(provider, field, hit=hit, verified=hit, latency_ms=latency_ms, cost=cost, failed=failed, region=region, vertical=vertical)
    _records_since_persist += 1
    if _records_since_persist >= _PERSIST_EVERY:
        _records_since_persist = 0
        persist_global_snapshot(intel().snapshot())


# ── negative cache ──────────────────────────────────────────────────────────
async def cached_misses(lead: dict, provider: str, fields: Iterable[str]) -> set[str]:
    """Fields this provider already failed to find for this lead and that are still inside their TTL."""
    key = lead_key(lead)
    if not key:
        return set()
    if (key, provider) not in _loaded_leads:
        _loaded_leads.add((key, provider))
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            rows = await asyncio.to_thread(lambda: _sb().table("enrichment_negative_cache").select("field,expires_at")
                                           .eq("lead_key", key).eq("provider", provider).gt("expires_at", now_iso).execute().data or [])
            for r in rows:
                exp = datetime.fromisoformat(str(r["expires_at"]).replace("Z", "+00:00")).timestamp()
                _misses[(key, provider, r["field"])] = exp
        except Exception as exc:
            logger.debug("negative cache read skipped: %s", type(exc).__name__)
    now = time.time()
    return {f for f in fields if _misses.get((key, provider, f), 0) > now}


async def record_misses(lead: dict, provider: str, fields: Iterable[str]) -> None:
    key = lead_key(lead)
    if not key:
        return
    rows = []
    for f in fields:
        exp = datetime.now(timezone.utc) + timedelta(days=field_ttl_days(f))
        _misses[(key, provider, f)] = exp.timestamp()
        rows.append({"lead_key": key, "provider": provider, "field": f, "expires_at": exp.isoformat()})
    if rows:
        try:
            await asyncio.to_thread(lambda: _sb().table("enrichment_negative_cache").upsert(rows, on_conflict="lead_key,provider,field").execute())
        except Exception as exc:
            logger.debug("negative cache write skipped: %s", type(exc).__name__)


# ── budgets and ledger ──────────────────────────────────────────────────────
def run_cap(tenant_id: str = "") -> float:
    """Credits one run may spend on paid enrichment; 0 means unlimited."""
    cap = float(os.getenv("LEADX_RUN_CREDIT_CAP", "0") or 0)
    if tenant_id:
        from app.integrations.prod.tenant_policy import load_policy
        tenant_cap = float(load_policy(tenant_id).get("run_credit_cap") or 0)
        cap = min(c for c in (cap, tenant_cap) if c > 0) if (cap > 0 or tenant_cap > 0) else 0
    return cap


def budget_allows(run_id: str, tenant_id: str, cost: float) -> bool:
    cap = run_cap(tenant_id)
    return cap <= 0 or _spent.get(run_id or "-", 0.0) + cost <= cap


def charge(run_id: str, credits: float) -> None:
    _spent[run_id or "-"] = _spent.get(run_id or "-", 0.0) + credits


def spent(run_id: str) -> float:
    return _spent.get(run_id or "-", 0.0)


async def ledger(tenant_id: str, run_id: str, lead: dict, provider: str, field: str, credits: float, hit: bool) -> None:
    try:
        row = {"tenant_id": tenant_id or None, "run_id": run_id or None, "lead_key": lead_key(lead) or None,
               "provider": provider, "field": field, "credits": credits, "hit": hit}
        await asyncio.to_thread(lambda: _sb().table("enrichment_spend_ledger").insert(row).execute())
    except Exception as exc:
        logger.debug("spend ledger write skipped: %s", type(exc).__name__)
