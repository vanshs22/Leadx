"""Numbers behind the fetching-intelligence dashboard (GET /crm/{tenant}/engine/fetching). Reads fetch_attempts and fetch_outcomes."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.integrations.prod.fetch_classify import CHALLENGE_CATEGORIES, NETWORK_CATEGORIES


def _pct(n: int, d: int) -> Optional[float]:
    return round(100 * n / d, 1) if d else None


def compute_fetching(sb, tenant_id: str, *, date_from: Optional[str] = None, date_to: Optional[str] = None, provider: Optional[str] = None,
                     category: Optional[str] = None, source: Optional[str] = None, limit: int = 20000) -> dict:
    date_from = date_from or (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    def q(table: str, cols: str):
        x = sb.table(table).select(cols).eq("tenant_id", tenant_id).gte("created_at", date_from)
        if date_to:
            x = x.lte("created_at", date_to)
        if source:
            x = x.eq("discovery_source", source)
        if category:
            x = x.eq("category", category)
        return x

    attempts = q("fetch_attempts", "provider,category,kind,ok,elapsed_ms,cost,domain,http_status").limit(limit).execute().data or []
    if provider:
        attempts = [a for a in attempts if a["provider"] == provider]
    outcomes_q = q("fetch_outcomes", "id,domain,website_fetched,category,challenge,acquisition,completeness,elapsed_ms,cost,tokens,provider,needs_human,human_status,recovery_sources,discovery_source")
    outcomes = outcomes_q.limit(limit).execute().data or []
    if provider:
        outcomes = [o for o in outcomes if o.get("provider") == provider]
    n = len(outcomes)
    cats = Counter(o.get("category") or "unknown" for o in outcomes if not o.get("website_fetched"))
    fetched = sum(1 for o in outcomes if o.get("website_fetched"))
    recovered = sum(1 for o in outcomes if o.get("acquisition") == "recovered")
    hint_only = sum(1 for o in outcomes if o.get("acquisition") == "hint_only")
    corroborated = sum(1 for o in outcomes if o.get("acquisition") == "corroborated")
    enriched = sum(1 for o in outcomes if o.get("acquisition") in ("fetched", "recovered"))
    cost = sum(float(o.get("cost") or 0) for o in outcomes)
    by_provider: dict[str, dict] = {}
    for a in attempts:
        p = by_provider.setdefault(a["provider"], {"attempts": 0, "ok": 0, "ms": 0.0, "cost": 0.0})
        p["attempts"] += 1; p["ok"] += int(bool(a["ok"])); p["ms"] += float(a.get("elapsed_ms") or 0); p["cost"] += float(a.get("cost") or 0)
    for p in by_provider.values():
        p["success_rate_pct"] = _pct(p["ok"], p["attempts"]); p["avg_ms"] = round(p.pop("ms") / p["attempts"]) if p["attempts"] else None; p["cost"] = round(p["cost"], 4)
    dom: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "failures": 0, "last_category": None})
    for a in attempts:
        d = dom[a["domain"]]
        d["attempts"] += 1
        if not a["ok"]:
            d["failures"] += 1; d["last_category"] = a["category"]
    worst = sorted(({"domain": k, **v} for k, v in dom.items() if v["failures"]), key=lambda r: (-r["failures"], r["domain"]))[:15]
    comp = [float(o["completeness"]) for o in outcomes if o.get("completeness") is not None]
    secs = [float(o["elapsed_ms"]) / 1000 for o in outcomes if o.get("elapsed_ms")]
    return {
        "window": {"from": date_from, "to": date_to}, "filters": {"provider": provider, "category": category, "source": source},
        "websites_attempted": n, "successful_fetches": fetched, "fetch_success_pct": _pct(fetched, n),
        "captcha_blocked": sum(v for k, v in cats.items() if k in CHALLENGE_CATEGORIES or k in ("http_403", "http_429")),
        "challenge_breakdown": {k: v for k, v in cats.items() if k in CHALLENGE_CATEGORIES or k in ("http_403", "http_429")},
        "http_failures": sum(v for k, v in cats.items() if k.startswith("http_") and k not in ("http_403", "http_429")),
        "timeout_failures": cats.get("timeout", 0), "dns_failures": cats.get("dns", 0),
        "other_network_failures": sum(v for k, v in cats.items() if k in NETWORK_CATEGORIES and k not in ("timeout", "dns")),
        "failure_categories": dict(cats.most_common()),
        "alt_source_recoveries": recovered, "alt_recovery_pct_of_attempted": _pct(recovered, n), "hint_only": hint_only, "corroborated": corroborated,
        "acquisition_success_pct": _pct(fetched + recovered, n),
        "avg_extraction_completeness_pct": round(100 * sum(comp) / len(comp), 1) if comp else None,
        "avg_fetch_seconds": round(sum(secs) / len(secs), 2) if secs else None,
        "cost": {"total_credits": round(cost, 4), "avg_per_enriched_lead": round(cost / enriched, 4) if enriched else None, "enriched_leads": enriched},
        "ai_tokens": sum(int(o.get("tokens") or 0) for o in outcomes),
        "by_provider": by_provider, "problem_domains": worst,
        "needs_human_verification": sum(1 for o in outcomes if o.get("needs_human") and o.get("human_status") == "pending"),
        "sources": sorted({o.get("discovery_source") for o in outcomes if o.get("discovery_source")}),
    }


def pending_verification(sb, tenant_id: str, limit: int = 100) -> list[dict]:
    rows = (sb.table("fetch_outcomes").select("id,domain,category,challenge,created_at,lead_key,human_status").eq("tenant_id", tenant_id)
            .eq("needs_human", True).eq("human_status", "pending").order("created_at", desc=True).limit(limit).execute().data or [])
    return rows


def resolve_verification(sb, tenant_id: str, outcome_id: str, action: str) -> Optional[dict]:
    """action: resolved (operator checked the site by hand), dismissed, or retry (clear the domain cooldown so the next run fetches it again)."""
    rows = sb.table("fetch_outcomes").select("id,domain").eq("id", outcome_id).eq("tenant_id", tenant_id).limit(1).execute().data or []
    if not rows:
        return None
    if action == "retry":
        from app.integrations.prod.fetch_engine import get_engine
        get_engine().clear_cooldown(rows[0]["domain"])
    sb.table("fetch_outcomes").update({"human_status": "retry_requested" if action == "retry" else action}).eq("id", outcome_id).eq("tenant_id", tenant_id).execute()
    return {"id": outcome_id, "domain": rows[0]["domain"], "human_status": "retry_requested" if action == "retry" else action}
