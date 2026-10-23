"""
Export leads as Instantly / Smartlead / Lemlist-ready CSV.
Closes the 'Apollo has sequences' gap for local sellers.
"""
from __future__ import annotations

import csv
import io
from typing import Any


def leads_to_instantly_csv(leads: list[dict[str, Any]]) -> str:
    """
    Columns Instantly/Smartlead commonly accept.
    """
    buf = io.StringIO()
    fields = [
        "email",
        "first_name",
        "last_name",
        "company_name",
        "phone",
        "website",
        "custom_score",
        "custom_tier",
        "custom_intent",
        "custom_pitch",
        "custom_city",
        "custom_booking",
        "linkedin_url",
    ]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for l in leads:
        owner = l.get("owner_name") or l.get("decision_maker_name") or ""
        parts = owner.strip().split(None, 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        w.writerow({
            "email": l.get("email") or "",
            "first_name": first,
            "last_name": last,
            "company_name": l.get("name") or l.get("company_name") or "",
            "phone": l.get("phone_e164") or l.get("phone") or "",
            "website": l.get("website") or "",
            "custom_score": l.get("score") or l.get("total_score") or "",
            "custom_tier": l.get("tier") or "",
            "custom_intent": l.get("intent_score") or "",
            "custom_pitch": l.get("pitch_line") or "",
            "custom_city": l.get("city") or "",
            "custom_booking": l.get("booking_platform") or "",
            "linkedin_url": l.get("linkedin_url") or "",
        })
    return buf.getvalue()
