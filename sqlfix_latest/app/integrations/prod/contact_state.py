"""Single source of truth for "is this lead contactable?" used by the gate, pipeline and verifier.

Why this exists: the quality gate, the pipeline stream and the legacy gate each had their own copy of
"email_status in {valid, deliverable}". On free hosts outbound SMTP (port 25) is blocked, so the local probe
can never reach `valid`; emails stop at MX-confirmed. Every copy then rejected every email, and with the data
quality default (50) below the gate threshold (55) the gate passed zero leads.

Levels (strongest first):
  valid     - mailbox confirmed by SMTP probe / provider
  probable  - MX confirmed, not role/disposable, on the lead's own website domain, SMTP could not be probed
              (opt out with LEADX_ACCEPT_PROBABLE_EMAIL=off, widen to any domain with =any)
"""
from __future__ import annotations
import os
from typing import Any

from app.integrations.prod.utils import DISPOSABLE_DOMAINS, EMAIL_RE, is_valid_email, normalize_phone

VALID = {"valid", "deliverable"}
INVALID = {"invalid", "undeliverable", "do_not_mail", "spamtrap"}
DISPOSABLE = {"disposable"}
UNAVAILABLE = {"", "unknown", "unavailable", "unverified", "no_mx_or_unresolved", "shape_valid"}
_FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "proton.me", "protonmail.com", "live.com", "msn.com"}


def _domain(value: str) -> str:
    v = (value or "").lower().strip()
    v = v.split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    return v[4:] if v.startswith("www.") else v


def probable_mode() -> str:
    return (os.getenv("LEADX_ACCEPT_PROBABLE_EMAIL", "domain_match") or "domain_match").strip().lower()


def _email_observations(lead: dict[str, Any]) -> list[dict[str, Any]]:
    ev = lead.get("email_verification")
    return (ev.get("observations") if isinstance(ev, dict) else None) or lead.get("email_observations") or []


def email_reason(lead: dict[str, Any]) -> str:
    """Return a safe, stable contact decision reason for the lead's email."""
    email = str(lead.get("email") or "").strip()
    if not email:
        return "unavailable"
    syntax_valid = bool(EMAIL_RE.fullmatch(email)) and "/" not in email and "\\\\" not in email and "://" not in email
    if not syntax_valid:
        return "invalid"
    status = str(lead.get("email_status") or "").strip().lower()
    observations = [o for o in _email_observations(lead) if isinstance(o, dict)]
    observed_statuses = {str(o.get("status") or "").strip().lower() for o in observations}
    if status in DISPOSABLE or "disposable" in observed_statuses or email.rsplit("@", 1)[1].lower() in DISPOSABLE_DOMAINS:
        return "disposable"
    if not is_valid_email(email):
        return "invalid"
    if status == "unavailable":
        return "unavailable"
    if status in INVALID or observed_statuses & INVALID or lead.get("email_valid") is False:
        return "invalid"
    if status in VALID or (not status and lead.get("email_valid") is True):
        return "valid"
    if status == "probable":
        return "probable"
    mx_ok = any(
        str(o.get("status") or "").lower() == "syntax_mx_valid" or o.get("mx_found") is True
        for o in observations
    )
    if not mx_ok:
        return "unavailable"
    mode = probable_mode()
    if mode in {"off", "0", "false", "no", "never"}:
        return "unavailable"
    dom = email.rsplit("@", 1)[1].lower()
    if dom in _FREE_MAIL:
        return "unavailable"
    if mode == "any":
        return "probable"
    site = _domain(str(lead.get("website") or lead.get("website_domain") or ""))
    return "probable" if site and (dom == site or dom.endswith("." + site) or site.endswith("." + dom)) else "unavailable"


def email_level(lead: dict[str, Any]) -> str | None:
    """Return only the usable email levels: ``valid`` or ``probable``."""
    reason = email_reason(lead)
    return reason if reason in {"valid", "probable"} else None


def _phone_value(lead: dict[str, Any]) -> str:
    return str(
        lead.get("phone") or lead.get("phone_e164") or lead.get("phone_normalized")
        or lead.get("phone_from_web") or ""
    ).strip()


def phone_reason(lead: dict[str, Any]) -> str:
    """Return a safe phone state without trusting a valid flag over a bad value."""
    value = _phone_value(lead)
    status = str(lead.get("phone_status") or "").strip().lower()
    if value and normalize_phone(value) is None and not lead.get("phone_verified"):
        return "invalid"
    if status in INVALID:
        return "invalid"
    if status == "valid" or lead.get("phone_verified") is True:
        return "valid"
    return "unavailable"


def phone_ok(lead: dict[str, Any]) -> bool:
    return phone_reason(lead) == "valid"


def email_ok(lead: dict[str, Any]) -> bool:
    return email_level(lead) is not None


def has_contact(lead: dict[str, Any]) -> bool:
    return email_ok(lead) or phone_ok(lead)


def derive_data_quality(lead: dict[str, Any]) -> float:
    """0-100 quality from evidence actually present. Used only when no explicit score was recorded."""
    q = 25.0
    lvl = email_level(lead)
    if lvl == "valid": q += 25
    elif lvl == "probable": q += 15
    if phone_ok(lead): q += 20
    if lead.get("website") or lead.get("website_domain"): q += 8
    if lead.get("address"): q += 5
    if lead.get("enrichment_ok"): q += 8
    if lead.get("services") or lead.get("has_booking") or lead.get("booking_platform"): q += 4
    if lead.get("owner_name") or lead.get("decision_maker_name"): q += 5
    if len(lead.get("sources_hit") or []) >= 2: q += 4
    return max(0.0, min(100.0, q))
