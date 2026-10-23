"""End-to-end revenue analytics: discovery -> delivery -> contact -> reply -> meeting -> deal -> revenue."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from app.memory.supabase_client import get_supabase


def _sb(): return get_supabase()
def _num(v):
    try: return float(v or 0)
    except Exception: return 0.0

def tenant_funnel(tenant_id: str, days: int = 90) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat(); sb = _sb()
    def rows(table, fields="*"):
        try: return sb.table(table).select(fields).eq("tenant_id", tenant_id).gte("created_at", since).limit(10000).execute().data or []
        except Exception: return []
    assignments = rows("lead_assignments", "id,lead_id,status,created_at")
    replies = rows("email_replies", "id,assignment_id,category,received_at,created_at")
    deals = rows("deals", "id,source_assignment_id,status,value,actual_close_date,created_at")
    # No `meetings` table exists in the schema; a meeting is a deal that reached the meeting stage.
    meetings = [d for d in deals if d.get("status") in ("meeting","scheduled","held","completed","showed")]
    stages = defaultdict(int)
    stages["discovered_or_assigned"] = len(assignments)
    stages["contacted"] = sum(1 for a in assignments if a.get("status") not in ("new", None))
    stages["replied"] = len({r.get("assignment_id") for r in replies if r.get("assignment_id")})
    stages["positive_replies"] = len({r.get("assignment_id") for r in replies if r.get("category") == "positive"})
    stages["meetings"] = sum(1 for m in meetings if m.get("status") in ("scheduled","held","completed","showed"))
    stages["opportunities"] = sum(1 for d in deals if d.get("status") == "open") + sum(1 for d in deals if d.get("status") == "won")
    stages["won"] = sum(1 for d in deals if d.get("status") == "won")
    revenue = sum(_num(d.get("value")) for d in deals if d.get("status") == "won")
    conversion = {k: round(v / stages["discovered_or_assigned"] * 100, 2) if stages["discovered_or_assigned"] else 0 for k,v in stages.items()}
    return {"ok": True, "window_days": days, "funnel": dict(stages), "conversion_pct": conversion, "revenue_won": round(revenue,2), "avg_won_value": round(revenue / stages["won"],2) if stages["won"] else 0, "reply_rate_pct": round(stages["replied"] / stages["contacted"] * 100,2) if stages["contacted"] else 0, "positive_rate_pct": round(stages["positive_replies"] / stages["replied"] * 100,2) if stages["replied"] else 0}
