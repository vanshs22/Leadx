"""Multi-source signal fusion with recency, source independence and conflict handling."""
from __future__ import annotations

from datetime import datetime, timezone
from math import prod


def _age_days(ts) -> float:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if not d.tzinfo: d = d.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)
    except Exception:
        return 9999.0


def fuse_signals(signals: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for s in signals or []:
        groups.setdefault(str(s.get("signal_type") or s.get("type") or "unknown"), []).append(s)
    fused = []
    for kind, rows in groups.items():
        weighted = []
        sources = set()
        for r in rows:
            age = _age_days(r.get("observed_at") or r.get("created_at"))
            decay = 0.5 ** (age / max(1, float(r.get("half_life", 30))))
            conf = max(0.05, min(1.0, float(r.get("confidence", 0.6))))
            strength = max(0.0, min(1.0, float(r.get("strength", conf)))) * conf * decay
            weighted.append(strength); sources.add(str(r.get("source") or "unknown"))
        independent_bonus = min(0.25, max(0, len(sources) - 1) * 0.08)
        combined = 1 - prod(1 - x for x in weighted) if weighted else 0
        fused.append({"signal_type": kind, "strength": round(min(1, combined + independent_bonus), 4), "sources": sorted(sources), "observations": rows})
    fused.sort(key=lambda x: x["strength"], reverse=True)
    p = 1.0
    for x in fused: p *= 1 - x["strength"]
    return {"timing_score": round((1 - p) * 100), "signals": fused, "signal_count": len(fused), "independent_sources": len({s for x in fused for s in x["sources"]})}
