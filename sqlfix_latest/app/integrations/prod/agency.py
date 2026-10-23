"""
Agency mode: sub-accounts + white-label client reports.

An agency tenant can own child tenants (its clients). Each child is a fully isolated
tenant (own leads, CRM, integrations); the parent only gets *management* rights and
a rolled-up view. Quota is carved out of the parent's monthly lead pool so an agency
can't hand out more than it bought.

Reports are rendered as self-contained HTML (print-to-PDF ready) with the agency's
own brand, and can be shared through an expiring, HMAC-signed link that exposes
that one report only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.plans import PLANS, normalize_plan

MAX_CHILDREN_BY_PLAN = {key: int(spec["max_children"]) for key, spec in PLANS.items()}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ------------------------------------------------------------ sub-accounts
def max_children(plan: str) -> int:
    return MAX_CHILDREN_BY_PLAN.get(normalize_plan(plan, default="starter"), 0)


def allocatable_quota(parent_limit: int, children_limits: list[int], parent_reserve_pct: float = 0.0) -> int:
    """Leads/month still available to hand to a new client."""
    reserve = int(parent_limit * max(0.0, min(1.0, parent_reserve_pct)))
    return max(0, parent_limit - reserve - sum(max(0, int(x or 0)) for x in children_limits))


def validate_child_request(parent: dict, existing_children: list[dict], name: str, quota: int) -> tuple[bool, str]:
    if not name or not name.strip() or len(name.strip()) > 120:
        return False, "invalid_name"
    if parent.get("parent_tenant_id"):
        return False, "sub_accounts_cannot_have_children"     # single level keeps isolation simple
    cap = max_children(parent.get("plan", "starter"))
    if len(existing_children) >= cap:
        return False, "child_limit_reached_for_plan" if cap else "plan_does_not_include_sub_accounts"
    if quota < 0:
        return False, "invalid_quota"
    free = allocatable_quota(int(parent.get("leads_per_month_limit") or 0),
                             [int(c.get("leads_per_month_limit") or 0) for c in existing_children])
    if quota > free:
        return False, f"quota_exceeds_available:{free}"
    return True, "ok"


def can_manage_child(ctx_tenant_id: str, ctx_role: str, child: dict) -> bool:
    """Parent owner/admin only; the child's own users are unaffected."""
    return (str(child.get("parent_tenant_id") or "") == str(ctx_tenant_id)
            and ctx_role in ("owner", "admin"))


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "client"
    return s


def rollup(children: list[dict], stats_by_child: dict[str, dict]) -> dict:
    tot = {"delivered": 0, "booked": 0, "won": 0, "revenue": 0.0}
    rows = []
    for c in children:
        s = stats_by_child.get(str(c["id"]), {})
        for k in tot:
            tot[k] += s.get(k, 0) or 0
        rows.append({"id": c["id"], "name": c.get("name"), "quota": c.get("leads_per_month_limit"),
                     "status": c.get("status"), **{k: s.get(k, 0) for k in tot},
                     "booked_rate": round(s.get("booked", 0) / s["delivered"], 4) if s.get("delivered") else None})
    return {"clients": rows, "totals": tot}


# ------------------------------------------------------------ branding
def sanitize_branding(b: dict) -> dict:
    logo = str(b.get("brand_logo_url") or "")
    return {
        "name": (b.get("brand_name") or "Your Agency")[:80],
        "logo_url": logo if logo.startswith("https://") and len(logo) < 500 and '"' not in logo and " " not in logo else "",
        "primary": b.get("brand_primary_color") if _HEX.match(str(b.get("brand_primary_color") or "")) else "#6366f1",
        "accent": b.get("brand_accent_color") if _HEX.match(str(b.get("brand_accent_color") or "")) else "#22c55e",
        "footer": (b.get("report_footer") or "")[:200],
    }


def _pct(n: Any, d: Any) -> str:
    try:
        return f"{100 * float(n) / float(d):.1f}%" if float(d) else "—"
    except (TypeError, ValueError):
        return "—"


def render_report_html(branding: dict, client_name: str, period: str, metrics: dict,
                       top_leads: Optional[list[dict]] = None, insights: Optional[list[str]] = None) -> str:
    """Self-contained, print-ready, XSS-safe (every dynamic value is escaped)."""
    b = sanitize_branding(branding)
    e = html.escape
    m = metrics or {}
    kpis = [
        ("Leads delivered", m.get("delivered", 0)), ("Contacted", m.get("contacted", 0)),
        ("Meetings booked", m.get("booked", 0)), ("Won", m.get("won", 0)),
        ("Booking rate", _pct(m.get("booked"), m.get("delivered"))),
        ("Cost per meeting", f"${m['cost_per_meeting']:,.0f}" if m.get("cost_per_meeting") else "—"),
    ]
    tiles = "".join(f'<div class="k"><div class="v">{e(str(v))}</div><div class="l">{e(l)}</div></div>' for l, v in kpis)
    rows = "".join(
        f"<tr><td>{e(str(l.get('name','')))}</td><td>{e(str(l.get('city','')))}</td>"
        f"<td>{e(str(l.get('why_now','')))}</td><td>{e(str(l.get('outcome','—')))}</td></tr>"
        for l in (top_leads or [])[:15])
    ins = "".join(f"<li>{e(str(i))}</li>" for i in (insights or [])[:8])
    logo = f'<img src="{e(b["logo_url"])}" alt="" style="height:36px">' if b["logo_url"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(client_name)} — {e(period)} report</title>
<style>:root{{--p:{b['primary']};--a:{b['accent']}}}body{{font:15px/1.5 system-ui,sans-serif;margin:0;color:#111;background:#fff}}
.w{{max-width:860px;margin:0 auto;padding:32px}}header{{display:flex;align-items:center;gap:12px;border-bottom:4px solid var(--p);padding-bottom:16px}}
h1{{font-size:22px;margin:0}}.sub{{color:#555}}.g{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0}}
.k{{border:1px solid #e5e7eb;border-radius:10px;padding:14px}}.v{{font-size:26px;font-weight:700;color:var(--p)}}.l{{color:#555;font-size:13px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #eee;font-size:13px}}
th{{background:#f9fafb}}h2{{font-size:16px;border-left:4px solid var(--a);padding-left:8px;margin-top:28px}}
footer{{margin-top:36px;color:#777;font-size:12px;border-top:1px solid #eee;padding-top:12px}}
@media(max-width:600px){{.g{{grid-template-columns:repeat(2,1fr)}}}}@media print{{.w{{padding:0}}}}</style></head>
<body><div class="w"><header>{logo}<div><h1>{e(client_name)} — Performance report</h1><div class="sub">{e(b['name'])} · {e(period)}</div></div></header>
<div class="g">{tiles}</div>
{f'<h2>What we learned</h2><ul>{ins}</ul>' if ins else ''}
{f'<h2>Top opportunities</h2><table><tr><th>Business</th><th>City</th><th>Why now</th><th>Outcome</th></tr>{rows}</table>' if rows else ''}
<footer>{e(b['footer'] or 'Prepared by ' + b['name'])} · Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}</footer></div></body></html>"""


# ------------------------------------------------------------ share links
def _secret() -> bytes:
    s = os.getenv("REPORT_LINK_SECRET") or os.getenv("SUPABASE_SERVICE_KEY") or ""
    if not s:
        raise RuntimeError("REPORT_LINK_SECRET (or SUPABASE_SERVICE_KEY) must be set to sign report links")
    return s.encode()


def sign_report_token(tenant_id: str, report_id: str, ttl_s: int = 7 * 86400, now: Optional[float] = None) -> str:
    payload = json.dumps({"t": tenant_id, "r": report_id, "e": int((now or time.time()) + ttl_s)}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_report_token(token: str, now: Optional[float] = None) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if int(data["e"]) < (now or time.time()):
            return None
        return {"tenant_id": data["t"], "report_id": data["r"]}
    except Exception:
        return None
