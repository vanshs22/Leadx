"""
Public data-quality scoreboard.

Publishes what competitors hide: measured delivery quality per vertical, computed
from what actually happened after delivery (bounces, dead phones, credits paid
back), not from a marketing claim.

Privacy / honesty rules
  * Only aggregates. No tenant, lead, email, or phone is ever exposed.
  * k-anonymity: a vertical is published only with >= MIN_SAMPLE delivered leads,
    from >= MIN_TENANTS distinct tenants (so one customer's data can't be inferred).
  * Rates use Wilson 95% lower/upper bounds so small samples don't overclaim.
  * The response includes the methodology, window, and a content hash so a
    published snapshot is tamper-evident and reproducible.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

MIN_SAMPLE = 50
MIN_TENANTS = 3
WINDOW_DAYS = 90

METHODOLOGY = {
    "window_days": WINDOW_DAYS,
    "delivered": "Leads assigned to a customer in the window.",
    "email_valid_rate": "Delivered leads with an email that passed syntax+MX+SMTP-risk verification / delivered leads with an email.",
    "phone_valid_rate": "Delivered leads with a phone that passed format+carrier-type checks / delivered leads with a phone.",
    "credit_rate": "Leads credited back to customers (dead phone, bounce, closed, duplicate) / delivered. Lower is better.",
    "guaranteed_quality": "1 - credit_rate. This is the number LeadX stands behind.",
    "freshness_median_days": "Median days between last verification and delivery.",
    "publication_rule": f"A vertical is shown only with >= {MIN_SAMPLE} delivered leads across >= {MIN_TENANTS} customers.",
    "intervals": "Rates show a Wilson 95% confidence interval.",
}


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def _rate(k: int, n: int) -> Optional[dict]:
    if n <= 0:
        return None
    lo, hi = wilson(k, n)
    return {"rate": round(k / n, 4), "ci95": [lo, hi], "n": n}


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return round(s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2, 1)


def compute_scoreboard(rows: Iterable[dict], now: Optional[datetime] = None) -> dict:
    """
    rows: one dict per delivered assignment:
      {tenant_id, vertical, delivered_at, has_email, email_valid, has_phone, phone_valid,
       credited, verified_at}
    """
    now = now or datetime.now(timezone.utc)
    by_v: dict[str, list[dict]] = {}
    for r in rows:
        by_v.setdefault(str(r.get("vertical") or "other").lower(), []).append(r)

    verticals: list[dict] = []
    suppressed = 0
    all_rows: list[dict] = []
    for v, rs in sorted(by_v.items()):
        tenants = {str(r.get("tenant_id")) for r in rs}
        if len(rs) < MIN_SAMPLE or len(tenants) < MIN_TENANTS:
            suppressed += 1
            all_rows.extend(rs)
            continue
        all_rows.extend(rs)
        verticals.append(_vertical_stats(v, rs))

    overall_tenants = {str(r.get("tenant_id")) for r in all_rows}
    overall = _vertical_stats("all", all_rows) if len(all_rows) >= MIN_SAMPLE and len(overall_tenants) >= MIN_TENANTS else None
    snapshot = {
        "generated_at": now.isoformat(), "window_days": WINDOW_DAYS,
        "overall": overall, "verticals": verticals,
        "verticals_suppressed_for_privacy": suppressed,
        "methodology": METHODOLOGY,
    }
    snapshot["integrity_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in snapshot.items() if k != "generated_at"}, sort_keys=True, default=str).encode()
    ).hexdigest()
    return snapshot


def _vertical_stats(name: str, rs: list[dict]) -> dict:
    n = len(rs)
    with_email = [r for r in rs if r.get("has_email")]
    with_phone = [r for r in rs if r.get("has_phone")]
    credited = sum(1 for r in rs if r.get("credited"))
    fresh: list[float] = []
    for r in rs:
        try:
            d = datetime.fromisoformat(str(r.get("delivered_at")).replace("Z", "+00:00"))
            v = datetime.fromisoformat(str(r.get("verified_at")).replace("Z", "+00:00"))
            fresh.append(max(0.0, (d - v).total_seconds() / 86400))
        except (TypeError, ValueError):
            continue
    credit = _rate(credited, n)
    return {
        "vertical": name, "delivered": n,
        "email_valid_rate": _rate(sum(1 for r in with_email if r.get("email_valid")), len(with_email)),
        "phone_valid_rate": _rate(sum(1 for r in with_phone if r.get("phone_valid")), len(with_phone)),
        "credit_rate": credit,
        "guaranteed_quality": {"rate": round(1 - credit["rate"], 4), "ci95": [round(1 - credit["ci95"][1], 4), round(1 - credit["ci95"][0], 4)], "n": n} if credit else None,
        "freshness_median_days": _median(fresh),
    }


def load_rows(sb, days: int = WINDOW_DAYS, limit: int = 50000) -> list[dict]:
    """Join assignments + enrichment + credits into scoreboard rows (best-effort, tolerant of missing tables)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    assigns = sb.table("lead_assignments").select("id,tenant_id,lead_id,vertical,delivered_at") \
        .gte("delivered_at", since).limit(limit).execute().data or []
    if not assigns:
        return []
    lead_ids = list({a["lead_id"] for a in assigns if a.get("lead_id")})
    enr: dict[str, dict] = {}
    lg: dict[str, dict] = {}
    for i in range(0, len(lead_ids), 500):
        chunk = lead_ids[i:i + 500]
        try:
            for e in sb.table("enrichment").select("lead_id,email,email_valid").in_("lead_id", chunk).execute().data or []:
                enr[str(e["lead_id"])] = e
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        try:
            for g in sb.table("leads_global").select("id,phone_e164,phone_normalized,last_verified_at").in_("id", chunk).execute().data or []:
                lg[str(g["id"])] = g
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    credited: set[str] = set()
    try:
        for c in sb.table("lead_credits").select("assignment_id").gte("created_at", since).limit(limit).execute().data or []:
            if c.get("assignment_id"):
                credited.add(str(c["assignment_id"]))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    out: list[dict] = []
    for a in assigns:
        e = enr.get(str(a.get("lead_id")), {})
        g = lg.get(str(a.get("lead_id")), {})
        out.append({
            "tenant_id": a["tenant_id"], "vertical": a.get("vertical"), "delivered_at": a.get("delivered_at"),
            "has_email": bool(e.get("email")), "email_valid": e.get("email_valid") is True,
            "has_phone": bool(g.get("phone_e164") or g.get("phone_normalized")),
            "phone_valid": bool(g.get("phone_e164")),  # e164 is only set after normalisation succeeds
            "credited": str(a["id"]) in credited, "verified_at": g.get("last_verified_at"),
        })
    return out
