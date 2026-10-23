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

VALID = {"valid", "deliverable"}
_FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "proton.me", "protonmail.com", "live.com", "msn.com"}


def _domain(value: str) -> str:
    v = (value or "").lower().strip()
    v = v.split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    return v[4:] if v.startswith("www.") else v


def probable_mode() -> str:
    return (os.getenv("LEADX_ACCEPT_PROBABLE_EMAIL", "domain_match") or "domain_match").strip().lower()


def email_level(lead: dict[str, Any]) -> str | None:
    """'valid' | 'probable' | None"""
    st = str(lead.get("email_status") or "").lower()
    if st in VALID:
        return "valid"
    email = str(lead.get("email") or "").lower().strip()
    if not email or "@" not in email:
        return None
    mode = probable_mode()
    if mode in {"off", "0", "false", "no", "never"}:
        return None
    if st in {"invalid", "undeliverable", "disposable", "do_not_mail", "spamtrap"}:
        return None
    # Email-only evidence. lead["verification_observations"] mixes phone + email, so never read it here:
    # a phone "invalid" must not veto an email.
    ev = lead.get("email_verification")
    obs = (ev.get("observations") if isinstance(ev, dict) else None) or lead.get("email_observations") or []
    mx_ok = any(str(o.get("status") or "").lower() == "syntax_mx_valid" or o.get("mx_found") is True for o in obs if isinstance(o, dict))
    if not mx_ok:
        return None
    if any(str(o.get("status") or "").lower() in {"invalid", "undeliverable", "disposable", "do_not_mail", "spamtrap"} for o in obs if isinstance(o, dict)):
        return None
    dom = email.rsplit("@", 1)[1]
    if dom in _FREE_MAIL:
        return None
    if mode == "any":
        return "probable"
    site = _domain(str(lead.get("website") or lead.get("website_domain") or ""))
    if site and (dom == site or dom.endswith("." + site) or site.endswith("." + dom)):
        return "probable"
    return None


def phone_ok(lead: dict[str, Any]) -> bool:
    return lead.get("phone_verified") is True or str(lead.get("phone_status") or "").lower() == "valid"


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
