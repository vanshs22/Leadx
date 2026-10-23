"""
Send policy — the "don't look like a blast" layer.

Four independent gates, all checked before a sequence step or bulk send
actually hands an email to a provider:

  1. Business hours in the *recipient's* timezone, inferred from the
     lead's state/geo. A rough US state→tz map is enough; being an hour
     off doesn't matter, sending at 3am does.
  2. Per-sending-domain daily cap, independent of how many sequence
     steps happen to be due. Provider caps (email_outreach._limit_for)
     protect the account; these protect the domain's reputation.
  3. Minimum gap between touches to the same lead across *all* channels,
     so the sequence job and a first-touch call can't both land today.
  4. Send-time spreading — a jitter delay so N due sends don't all fire
     on the same second at the top of the hour.

Warm-up: a domain with a `domain_warmup` row gets a cap that ramps from
start_cap to full_cap over ramp_days instead of full volume on day one.
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.send_policy")

# Business window, recipient-local, 24h clock.
BUSINESS_START_HOUR = int(os.getenv("SEND_BUSINESS_START_HOUR", "9"))
BUSINESS_END_HOUR = int(os.getenv("SEND_BUSINESS_END_HOUR", "17"))
SEND_WEEKENDS = os.getenv("SEND_WEEKENDS", "false").lower() in ("1", "true", "yes")
# Hours that must pass between any two outbound touches to one lead.
MIN_TOUCH_GAP_HOURS = int(os.getenv("MIN_TOUCH_GAP_HOURS", "24"))
# Default daily cap per sending domain when no warm-up row exists.
DEFAULT_DOMAIN_DAILY_CAP = int(os.getenv("DOMAIN_DAILY_CAP", "300"))
# Max jitter applied to each send so a batch spreads over a window.
SEND_JITTER_MAX_SECONDS = int(os.getenv("SEND_JITTER_MAX_SECONDS", "90"))

# Standard UTC offsets. DST is deliberately ignored — a one-hour drift
# for part of the year cannot push a 9–17 window outside business hours.
_STATE_TZ = {
    "AL":"America/Chicago","AK":"America/Anchorage","AZ":"America/Phoenix","AR":"America/Chicago","CA":"America/Los_Angeles","CO":"America/Denver","CT":"America/New_York","DE":"America/New_York","FL":"America/New_York","GA":"America/New_York","HI":"Pacific/Honolulu","ID":"America/Denver","IL":"America/Chicago","IN":"America/Indiana/Indianapolis","IA":"America/Chicago","KS":"America/Chicago","KY":"America/New_York","LA":"America/Chicago","ME":"America/New_York","MD":"America/New_York","MA":"America/New_York","MI":"America/Detroit","MN":"America/Chicago","MS":"America/Chicago","MO":"America/Chicago","MT":"America/Denver","NE":"America/Chicago","NV":"America/Los_Angeles","NH":"America/New_York","NJ":"America/New_York","NM":"America/Denver","NY":"America/New_York","NC":"America/New_York","ND":"America/Chicago","OH":"America/New_York","OK":"America/Chicago","OR":"America/Los_Angeles","PA":"America/New_York","RI":"America/New_York","SC":"America/New_York","SD":"America/Chicago","TN":"America/Chicago","TX":"America/Chicago","UT":"America/Denver","VT":"America/New_York","VA":"America/New_York","WA":"America/Los_Angeles","WV":"America/New_York","WI":"America/Chicago","WY":"America/Denver","DC":"America/New_York"
}
_STATE_NAMES = {"california":"CA","new york":"NY","new jersey":"NJ","texas":"TX","florida":"FL","illinois":"IL","pennsylvania":"PA","ohio":"OH","georgia":"GA","north carolina":"NC","michigan":"MI","arizona":"AZ","washington":"WA","massachusetts":"MA","virginia":"VA","colorado":"CO","nevada":"NV","oregon":"OR","connecticut":"CT","maryland":"MD","tennessee":"TN","missouri":"MO","wisconsin":"WI","minnesota":"MN","utah":"UT","indiana":"IN","louisiana":"LA","alabama":"AL","south carolina":"SC","kentucky":"KY","oklahoma":"OK","iowa":"IA","kansas":"KS","arkansas":"AR","mississippi":"MS","nebraska":"NE","idaho":"ID","new mexico":"NM","hawaii":"HI","alaska":"AK"}


def _iana_timezone(lead: dict[str, Any]) -> str:
    explicit = str(lead.get("timezone") or lead.get("iana_timezone") or "").strip()
    if explicit:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(explicit); return explicit
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    country = str(lead.get("country_code") or lead.get("country") or "").strip().upper()
    if country in {"IN","INDIA"}: return "Asia/Kolkata"
    if country in {"GB","UK","UNITED KINGDOM"}: return "Europe/London"
    if country in {"AU","AUSTRALIA"}: return "Australia/Sydney"
    if country in {"CA","CANADA"}: return "America/Toronto"
    if country in {"US","USA","UNITED STATES"}:
        st = state_from_lead(lead)
        return _STATE_TZ.get(st or "", "America/New_York")
    st = state_from_lead(lead)
    return _STATE_TZ.get(st or "", "UTC")


def _sb():
    return get_supabase()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------ timezone


def state_from_lead(lead: dict[str, Any]) -> Optional[str]:
    """Pull a 2-letter state out of whatever the lead happens to carry:
    an explicit column, a city/geo string, or the tail of an address."""
    for key in ("state", "region", "state_code"):
        v = (lead.get(key) or "").strip()
        if len(v) == 2 and v.upper() in _STATE_TZ:
            return v.upper()
        if v.lower() in _STATE_NAMES:
            return _STATE_NAMES[v.lower()]
    # Prefer explicit city/country context before scanning the full address.
    # "Washington" is ambiguous: Washington, DC is not Washington state, and
    # street names such as "Washington St" must never decide the state.
    city = str(lead.get("city") or "").strip().lower()
    if city in {"washington, dc", "washington dc", "district of columbia"}:
        return "DC"
    explicit_region = str(lead.get("region") or lead.get("state") or "").strip().lower()
    if explicit_region in {"dc", "d.c.", "district of columbia"}:
        return "DC"
    geo = " ".join(str(lead.get(k) or "") for k in ("geo", "location")).lower()
    if "washington, dc" in geo or "washington dc" in geo or "district of columbia" in geo:
        return "DC"
    # Only inspect address/full_address for an explicit postal-style state token.
    address_blob = " ".join(str(lead.get(k) or "") for k in ("address", "full_address", "location", "geo"))
    import re
    m = re.search(r",\s*([A-Z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?", address_blob)
    if m and m.group(1) in _STATE_TZ:
        return m.group(1)
    for name, code in _STATE_NAMES.items():
        if name in geo:
            return code
    # ", NJ 07030" style tails
    import re

    m = re.search(r",\s*([A-Z]{2})\b", " ".join(
        str(lead.get(k) or "") for k in ("address", "full_address", "location", "geo")
    ))
    if m and m.group(1) in _STATE_TZ:
        return m.group(1)
    return None


def utc_offset_hours(lead: dict[str, Any]) -> int:
    from zoneinfo import ZoneInfo
    dt = (_now()).astimezone(ZoneInfo(_iana_timezone(lead)))
    return int(dt.utcoffset().total_seconds() // 3600) if dt.utcoffset() else 0

def local_now(lead: dict[str, Any], now: Optional[datetime] = None) -> datetime:
    from zoneinfo import ZoneInfo
    base = now or _now()
    if base.tzinfo is None: base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(ZoneInfo(_iana_timezone(lead)))


def is_business_hours(lead: dict[str, Any], now: Optional[datetime] = None) -> bool:
    lt = local_now(lead, now)
    if not SEND_WEEKENDS and lt.weekday() >= 5:
        return False
    return BUSINESS_START_HOUR <= lt.hour < BUSINESS_END_HOUR


def next_business_window(lead: dict[str, Any], now: Optional[datetime] = None) -> datetime:
    """UTC instant of the next open using IANA timezone + DST."""
    from zoneinfo import ZoneInfo
    now = now or _now()
    tz = ZoneInfo(_iana_timezone(lead))
    lt = now.astimezone(tz)
    candidate = lt.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)
    if lt.hour >= BUSINESS_START_HOUR: candidate += timedelta(days=1)
    while not SEND_WEEKENDS and candidate.weekday() >= 5: candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


# ------------------------------------------------------------ domain caps


def sending_domain(from_email: str) -> str:
    return (from_email or "").split("@")[-1].strip().lower()


def warmup_cap(tenant_id: str, domain: str, today: Optional[date] = None) -> int:
    """Cap for today. Linear ramp start_cap → full_cap across ramp_days."""
    today = today or _now().date()
    try:
        rows = (
            _sb()
            .table("domain_warmup")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("domain", domain)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return DEFAULT_DOMAIN_DAILY_CAP
    if not rows:
        # Tenant SES settings are the authoritative product-level cap when
        # a tenant has configured a sending identity. Keep the conservative
        # default for tenants that have no email configuration yet.
        try:
            settings = (
                _sb().table("tenant_email_settings")
                .select("daily_limit")
                .eq("tenant_id", tenant_id)
                .eq("domain", domain)
                .limit(1).execute().data or []
            )
            if settings and settings[0].get("daily_limit") is not None:
                return max(0, int(settings[0]["daily_limit"]))
        except Exception:
            logger.warning("tenant email daily_limit lookup failed", exc_info=True)
        return DEFAULT_DOMAIN_DAILY_CAP
    w = rows[0]
    if w.get("paused"):
        return 0
    try:
        started = datetime.fromisoformat(str(w["started_on"])).date()
    except Exception:
        started = today
    day_n = max(0, (today - started).days)
    ramp = max(1, int(w.get("ramp_days") or 21))
    lo = int(w.get("start_cap") or 20)
    hi = int(w.get("full_cap") or DEFAULT_DOMAIN_DAILY_CAP)
    if day_n >= ramp:
        return hi
    return int(lo + (hi - lo) * (day_n / ramp))


def domain_sent_today(tenant_id: str, domain: str) -> int:
    try:
        rows = (
            _sb()
            .table("email_domain_daily")
            .select("sent_count")
            .eq("day", _now().date().isoformat())
            .eq("tenant_id", tenant_id)
            .eq("domain", domain)
            .limit(1)
            .execute()
            .data
            or []
        )
        return int(rows[0]["sent_count"]) if rows else 0
    except Exception:
        return 0


def bump_domain_sent(tenant_id: str, domain: str, n: int = 1) -> None:
    day = _now().date().isoformat()
    try:
        current = domain_sent_today(tenant_id, domain)
        _sb().table("email_domain_daily").upsert(
            {"day": day, "tenant_id": tenant_id, "domain": domain, "sent_count": current + n},
            on_conflict="day,tenant_id,domain",
        ).execute()
    except Exception:
        logger.warning("bump_domain_sent failed %s/%s", tenant_id, domain, exc_info=True)


def domain_under_cap(tenant_id: str, domain: str) -> tuple[bool, int, int]:
    """(allowed, sent_today, cap_today)"""
    if not domain:
        return True, 0, DEFAULT_DOMAIN_DAILY_CAP
    cap = warmup_cap(tenant_id, domain)
    sent = domain_sent_today(tenant_id, domain)
    return sent < cap, sent, cap



def reserve_domain_slot(tenant_id: str, domain: str) -> bool:
    """Atomically reserve one daily sender slot. Fail closed if DB guard is unavailable."""
    domain = sending_domain(domain)
    if not domain:
        return False
    cap = warmup_cap(tenant_id, domain)
    try:
        r = _sb().rpc("reserve_email_domain_slot", {"p_tenant_id": tenant_id, "p_domain": domain, "p_cap": int(cap)}).execute()
        return bool(r.data)
    except Exception:
        logger.error("atomic domain send guard unavailable", exc_info=True)
        return False


def release_domain_slot(tenant_id: str, domain: str) -> None:
    domain = sending_domain(domain)
    if not domain:
        return
    try:
        _sb().rpc("release_email_domain_slot", {"p_tenant_id": tenant_id, "p_domain": domain}).execute()
    except Exception:
        logger.error("domain slot release failed", exc_info=True)

def domain_remaining(tenant_id: str, domain: str) -> int:
    _, sent, cap = domain_under_cap(tenant_id, domain)
    return max(0, cap - sent)


def start_warmup(
    tenant_id: str,
    domain: str,
    *,
    start_cap: int = 20,
    full_cap: int = 500,
    ramp_days: int = 21,
) -> dict:
    try:
        _sb().table("domain_warmup").upsert(
            {
                "tenant_id": tenant_id,
                "domain": domain.lower(),
                "started_on": _now().date().isoformat(),
                "start_cap": start_cap,
                "full_cap": full_cap,
                "ramp_days": ramp_days,
                "paused": False,
            },
            on_conflict="tenant_id,domain",
        ).execute()
        return {"ok": True, "domain": domain, "today_cap": warmup_cap(tenant_id, domain.lower())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def warmup_status(tenant_id: str) -> list[dict]:
    try:
        rows = (
            _sb().table("domain_warmup").select("*").eq("tenant_id", tenant_id).execute().data or []
        )
    except Exception:
        return []
    out = []
    for w in rows:
        dom = w["domain"]
        cap = warmup_cap(tenant_id, dom)
        out.append({**w, "today_cap": cap, "sent_today": domain_sent_today(tenant_id, dom)})
    return out


# ------------------------------------------------------------ touch gap


def last_touch_at(assignment_id: str) -> Optional[datetime]:
    if not assignment_id:
        return None
    try:
        rows = (
            _sb()
            .table("lead_touches")
            .select("touched_at")
            .eq("assignment_id", assignment_id)
            .order("touched_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return None
    if not rows:
        return None
    try:
        return datetime.fromisoformat(str(rows[0]["touched_at"]).replace("Z", "+00:00"))
    except Exception:
        return None


def touch_gap_ok(assignment_id: str, hours: int = MIN_TOUCH_GAP_HOURS) -> bool:
    last = last_touch_at(assignment_id)
    if last is None:
        return True
    return (_now() - last) >= timedelta(hours=hours)


def record_touch(
    tenant_id: str, assignment_id: Optional[str], channel: str, meta: Optional[dict] = None
) -> None:
    try:
        _sb().table("lead_touches").insert(
            {
                "tenant_id": tenant_id,
                "assignment_id": assignment_id,
                "channel": channel,
                "meta": meta or {},
            }
        ).execute()
    except Exception:
        logger.warning("record_touch failed (%s)", channel, exc_info=True)


# ------------------------------------------------------------ combined gate


def jitter_seconds() -> float:
    return random.uniform(0, max(0, SEND_JITTER_MAX_SECONDS))


def can_send_now(
    tenant_id: str,
    lead: dict[str, Any],
    *,
    assignment_id: Optional[str] = None,
    from_email: str = "",
    respect_hours: bool = True,
) -> dict:
    """One call, all four gates. Returns
    {ok, reason, retry_after (iso|None), domain, sent_today, cap}."""
    domain = sending_domain(from_email)
    allowed, sent, cap = domain_under_cap(tenant_id, domain) if domain else (True, 0, 0)
    if not allowed:
        # Tomorrow's cap resets at UTC midnight, which is when the
        # email_domain_daily key rolls over.
        tomorrow = (_now() + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        return {
            "ok": False,
            "reason": "domain_daily_cap",
            "retry_after": tomorrow.isoformat(),
            "domain": domain,
            "sent_today": sent,
            "cap": cap,
        }
    if assignment_id and not touch_gap_ok(assignment_id):
        last = last_touch_at(assignment_id) or _now()
        return {
            "ok": False,
            "reason": "min_touch_gap",
            "retry_after": (last + timedelta(hours=MIN_TOUCH_GAP_HOURS)).isoformat(),
            "domain": domain,
            "sent_today": sent,
            "cap": cap,
        }
    if respect_hours and not is_business_hours(lead):
        return {
            "ok": False,
            "reason": "outside_business_hours",
            "retry_after": next_business_window(lead).isoformat(),
            "domain": domain,
            "sent_today": sent,
            "cap": cap,
        }
    return {
        "ok": True,
        "reason": "",
        "retry_after": None,
        "domain": domain,
        "sent_today": sent,
        "cap": cap,
    }
