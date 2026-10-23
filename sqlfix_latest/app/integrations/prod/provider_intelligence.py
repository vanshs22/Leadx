"""LeadX 14.0 Provider Intelligence.

Tracks provider/field outcomes, cost, latency and regional/vertical context and
produces adaptive ordering. Persistence is best-effort; execution never depends
on the analytics store being available.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import os

@dataclass
class ProviderMetric:
    calls: int = 0
    hits: int = 0
    verified_hits: int = 0
    failures: int = 0
    total_latency_ms: int = 0
    total_cost: float = 0.0
    def record(self, *, hit: bool, verified: bool = False, latency_ms: int = 0, cost: float = 0.0, failed: bool = False):
        self.calls += 1; self.hits += int(hit); self.verified_hits += int(verified); self.failures += int(failed)
        self.total_latency_ms += max(0, int(latency_ms)); self.total_cost += max(0.0, float(cost))
    def snapshot(self) -> dict[str, Any]:
        return {"calls": self.calls, "hits": self.hits, "verified_hits": self.verified_hits, "failures": self.failures,
                "hit_rate": round(self.hits / self.calls, 4) if self.calls else 0.0,
                "verified_rate": round(self.verified_hits / self.hits, 4) if self.hits else 0.0,
                "failure_rate": round(self.failures / self.calls, 4) if self.calls else 0.0,
                "avg_latency_ms": round(self.total_latency_ms / self.calls, 1) if self.calls else 0.0,
                "cost_per_hit": round(self.total_cost / self.hits, 5) if self.hits else None}

class ProviderIntelligence:
    def __init__(self, snapshot: dict[str, Any] | None = None):
        self.metrics: dict[str, ProviderMetric] = {}
        for key, value in (snapshot or {}).get("metrics", {}).items():
            self.metrics[key] = ProviderMetric(**{k: value.get(k, 0) for k in ("calls","hits","verified_hits","failures","total_latency_ms","total_cost")})
    def _key(self, provider: str, field: str, region: str = "global", vertical: str = "global") -> str:
        return f"{provider}:{field}:{region.lower()}:{vertical.lower()}"
    def record(self, provider: str, field: str, *, hit: bool, verified: bool = False, latency_ms: int = 0, cost: float = 0.0, failed: bool = False, region: str = "global", vertical: str = "global"):
        k = self._key(provider, field, region, vertical)
        self.metrics.setdefault(k, ProviderMetric()).record(hit=hit, verified=verified, latency_ms=latency_ms, cost=cost, failed=failed)
    def score(self, provider: str, field: str, *, base_prior: float = 0.3, cost: float = 1.0, region: str = "global", vertical: str = "global") -> float:
        m = self.metrics.get(self._key(provider, field, region, vertical)) or self.metrics.get(self._key(provider, field))
        if not m or m.calls < 5: hit = base_prior
        else: hit = m.hits / max(1, m.calls)
        verified = (m.verified_hits / m.hits) if m and m.hits else hit
        latency_penalty = min(0.25, ((m.total_latency_ms / m.calls) / 10000.0) if m and m.calls else 0.0)
        failure_penalty = (m.failures / m.calls) * 0.25 if m and m.calls else 0.0
        return max(0.01, (0.55 * hit + 0.35 * verified + 0.10 * min(1.0, 1.0 / max(0.01, cost))) - latency_penalty - failure_penalty)
    def rank(self, providers: list[Any], field: str, *, region: str = "global", vertical: str = "global") -> list[Any]:
        return sorted([p for p in providers if field in getattr(p, "fields", ())], key=lambda p: (-self.score(p.name, field, base_prior=getattr(p,"prior_hit_rate",0.3), cost=getattr(p,"cost",1.0), region=region, vertical=vertical), getattr(p,"cost",1.0), p.name))
    def snapshot(self) -> dict[str, Any]:
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "metrics": {k: v.__dict__ for k, v in self.metrics.items()}, "version": "14.0"}

def load_global_snapshot() -> dict[str, Any]:
    try:
        from app.memory.supabase_client import get_supabase
        rows = get_supabase().table("provider_intelligence_stats").select("snapshot").eq("id", "global").limit(1).execute().data or []
        return (rows[0].get("snapshot") or {}) if rows else {}
    except Exception:
        return {}

def persist_global_snapshot(snapshot: dict[str, Any]) -> None:
    try:
        from app.memory.supabase_client import get_supabase
        get_supabase().table("provider_intelligence_stats").upsert({"id":"global","snapshot":snapshot,"updated_at":datetime.now(timezone.utc).isoformat()}, on_conflict="id").execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
