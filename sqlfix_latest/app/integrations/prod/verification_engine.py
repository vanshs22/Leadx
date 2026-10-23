"""Independent verification gates for email, phone, domain and employment."""
from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def verify_email_shape(email: str) -> dict[str, Any]:
    e = (email or "").strip().lower()
    return {"valid_syntax": bool(EMAIL_RE.match(e)), "email": e or None}


def verify_lead(lead: dict) -> dict:
    out = dict(lead)
    checks = {}
    if lead.get("email"):
        checks["email"] = verify_email_shape(lead["email"])
    if lead.get("phone") or lead.get("phone_e164"):
        p = str(lead.get("phone_e164") or lead.get("phone"))
        checks["phone"] = {"valid_shape": len(re.sub(r"\D", "", p)) >= 7}
    if lead.get("website_domain"):
        checks["domain"] = {"valid_shape": "." in str(lead["website_domain"]), "domain": lead["website_domain"]}
    if lead.get("owner_name"):
        checks["employment"] = {"has_person_name": len(str(lead["owner_name"]).split()) >= 2}
    score = 0
    score += 25 if checks.get("email", {}).get("valid_syntax") else 0
    score += 20 if checks.get("phone", {}).get("valid_shape") else 0
    score += 20 if checks.get("domain", {}).get("valid_shape") else 0
    score += 15 if checks.get("employment", {}).get("has_person_name") else 0
    score += 10 if lead.get("website") else 0
    score += 10 if len(lead.get("sources_hit") or []) >= 2 else 0
    out["verification"] = {"checks": checks, "verification_score": min(100, score), "verified_at": lead.get("verified_at")}
    out["verification_score"] = min(100, score)
    return out
