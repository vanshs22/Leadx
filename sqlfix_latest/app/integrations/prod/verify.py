"""
Enrichment quality upgrades:
- Email MX / DNS validation (before awarding +22 score)
- Phone verification via Twilio Lookup (optional)
- Freshness decay for stale scores
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("lead_engine.verify")

# Cache MX results in-process to avoid hammering DNS
_mx_cache: dict[str, tuple[bool, float]] = {}
_MX_TTL = 3600  # 1 hour


async def email_has_mx(email: str) -> bool:
    """
    True if domain has MX or A record (deliverable-ish).
    Does NOT do SMTP RCPT (can get you blocked); MX is the safe production check.
    """
    if not email or "@" not in email:
        return False
    domain = email.split("@")[-1].lower().strip()
    if not domain or "." not in domain:
        return False

    now = time_monotonic()
    cached = _mx_cache.get(domain)
    if cached and (now - cached[1]) < _MX_TTL:
        return cached[0]

    ok = await asyncio.to_thread(_dns_mx_or_a, domain)
    _mx_cache[domain] = (ok, now)
    return ok


def time_monotonic() -> float:
    import time
    return time.monotonic()


def _dns_mx_or_a(domain: str) -> bool:
    try:
        import dns.resolver  # dnspython
        try:
            answers = dns.resolver.resolve(domain, "MX")
            if answers:
                return True
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        try:
            answers = dns.resolver.resolve(domain, "A")
            if answers:
                return True
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
        return False
    except ImportError:
        # Fallback: socket getaddrinfo
        import socket
        try:
            socket.getaddrinfo(domain, 25)
            return True
        except Exception:
            try:
                socket.getaddrinfo(domain, 80)
                return True
            except Exception:
                return False


async def verify_email_full(email: str) -> dict:
    """
    Returns {email, syntax_ok, mx_ok, valid}
    valid = syntax + not disposable + MX
    """
    from app.integrations.prod.utils import is_valid_email

    syntax = is_valid_email(email)
    if not syntax:
        return {"email": email, "syntax_ok": False, "mx_ok": False, "valid": False}
    mx = await email_has_mx(email)
    return {"email": email, "syntax_ok": True, "mx_ok": mx, "valid": mx}


async def verify_phone_twilio(phone_e164: str) -> dict:
    """
    Optional Twilio Lookup — confirms line type / validity.
    Requires TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN.
    Returns {valid, line_type, carrier, error?}
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token or not phone_e164:
        return {"valid": None, "skipped": True}

    # Normalize to E.164
    digits = re.sub(r"[^\d+]", "", phone_e164)
    if not digits.startswith("+"):
        digits = "+1" + re.sub(r"\D", "", digits)[-10:]

    try:
        import httpx
        url = f"https://lookups.twilio.com/v2/PhoneNumbers/{digits}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                url,
                params={"Fields": "line_type_intelligence"},
                auth=(sid, token),
            )
            if r.status_code == 404:
                return {"valid": False, "line_type": None, "error": "not_found"}
            if r.status_code != 200:
                return {"valid": None, "error": f"twilio_{r.status_code}", "skipped": True}
            data = r.json()
            line = (data.get("line_type_intelligence") or {}).get("type")
            # mobile / landline / fixedVoip often OK for outreach
            valid = line in ("mobile", "landline", "fixedVoip", "nonFixedVoip", "tollFree") or line is None
            return {
                "valid": valid,
                "line_type": line,
                "carrier": (data.get("line_type_intelligence") or {}).get("carrier_name"),
                "phone": data.get("phone_number") or digits,
            }
    except Exception as e:
        logger.warning("Twilio lookup failed: %s", e)
        return {"valid": None, "error": str(e), "skipped": True}


def score_freshness_multiplier(scored_at: Optional[datetime], half_life_days: int = 90) -> float:
    """
    Decay score weight for stale leads.
    Fresh = 1.0; after half_life_days ≈ 0.5; very old → approaches 0.25 floor.
    """
    if not scored_at:
        # Scoring right now (no prior timestamp) — full weight.
        # Decay only applies when re-using an old scored_at from DB.
        return 1.0
    if scored_at.tzinfo is None:
        scored_at = scored_at.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - scored_at).total_seconds() / 86400
    if age_days <= 14:
        return 1.0
    # exponential-ish decay
    import math
    factor = 0.5 ** (age_days / half_life_days)
    return max(0.25, min(1.0, factor))


async def apply_verification_to_lead(lead: dict) -> dict:
    """
    Mutates/returns lead with email_valid (MX) and optional phone_verified.
    Call after enrichment, before final scoring.
    """
    email = lead.get("email")
    if email:
        v = await verify_email_full(email)
        lead["email_valid"] = v["valid"]
        lead["email_mx_ok"] = v.get("mx_ok")
        if not v["valid"]:
            # Don't count invalid emails as enriched contact
            logger.debug("Email failed MX: %s", email)
    else:
        lead["email_valid"] = False

    phone = lead.get("phone_e164") or lead.get("phone")
    if phone and os.getenv("TWILIO_ACCOUNT_SID"):
        pv = await verify_phone_twilio(phone if str(phone).startswith("+") else f"+1{phone}")
        lead["phone_verified"] = pv.get("valid")
        lead["phone_line_type"] = pv.get("line_type")
    return lead
