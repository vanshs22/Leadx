"""
Rank & filter leads for delivery — high quality, contactable, non-redundant.

After enrich + score, only the best candidates should reach the client.
"""
from __future__ import annotations

from typing import Any, Optional

from app.integrations.prod.contact_state import email_level, phone_ok


def contactability(lead: dict) -> tuple[int, list[str]]:
    """0-100: can the client actually reach this business?"""
    score = 0
    reasons = []
    if phone_ok(lead):
        score += 40
        reasons.append("verified_phone")
    email_level_value = email_level(lead)
    if email_level_value:
        score += 35 if email_level_value == "valid" else 25
        reasons.append("verified_email" if email_level_value == "valid" else "probable_email")
    if lead.get("website"):
        score += 10
        reasons.append("website")
    if lead.get("linkedin_url") or (lead.get("social_links") or {}).get("linkedin"):
        score += 18  # social lane primary channel
        reasons.append("linkedin")
    if lead.get("instagram_handle") or lead.get("instagram_url") or (lead.get("social_links") or {}).get("instagram"):
        score += 16
        reasons.append("instagram")
    if lead.get("owner_name") or lead.get("decision_maker_name"):
        score += 7
        reasons.append("named_dm")
    return min(100, score), reasons


def composite_quality(lead: dict) -> int:
    """
    Single rank score for delivery order.
    Combines deterministic score, intent, discovery, contactability, multi-source.
    """
    base = int(lead.get("score") or lead.get("total_score") or 0)
    intent = int(lead.get("intent_score") or 0)
    disc = int(lead.get("discovery_quality") or 50)
    contact, _ = contactability(lead)
    multi = min(20, 8 * max(0, len(lead.get("sources_hit") or []) - 1))

    # Weighted blend
    return int(
        0.40 * base
        + 0.20 * intent
        + 0.15 * disc
        + 0.20 * contact
        + 0.05 * (multi * 5)  # multi already 0-20
    )


def is_contactable(lead: dict, require: str = "phone_or_email") -> bool:
    has_phone = phone_ok(lead)
    has_email = email_level(lead) is not None
    social = lead.get("social_links") if isinstance(lead.get("social_links"), dict) else {}
    has_social = bool(
        lead.get("linkedin_url")
        or lead.get("instagram_handle")
        or social.get("linkedin")
        or social.get("instagram")
    )
    if require == "phone":
        return has_phone
    if require == "email":
        return has_email
    if require == "phone_and_email":
        return has_phone and has_email
    if require in ("phone_or_email_or_social", "any"):
        return has_phone or has_email or has_social
    # phone_or_email (default)
    return has_phone or has_email


def is_deliverable(
    lead: dict,
    *,
    allow_tier_b: bool = False,
    allow_tier_c: bool = False,
    require_contact: str = "phone_or_email",
    min_composite: int = 55,
    min_score: int = 50,
) -> tuple[bool, str]:
    if (lead.get("business_status") or "").upper() == "CLOSED":
        return False, "closed"

    # Lane-aware gate: LinkedIn / Instagram can pass without phone
    try:
        from app.integrations.prod.source_lanes import is_deliverable_lane, annotate_lane, lead_lane
        annotate_lane(lead)
        lane = lead_lane(lead)
        if lane in ("linkedin", "instagram", "web") and not is_contactable(lead, require_contact):
            return False, "not_contactable"
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    tier = (lead.get("tier") or "C").upper()
    allowed = {"A"}
    if allow_tier_b: allowed.add("B")
    if allow_tier_c: allowed.add("C")
    if tier not in allowed:
        if not (allow_tier_b and tier == "B"):
            if tier == "C" and composite_quality(lead) < 70:
                return False, f"tier={tier}"
    if not is_contactable(lead, require_contact):
        return False, "not_contactable"
    if int(lead.get("score") or 0) < min_score and composite_quality(lead) < min_composite:
        return False, "low_score"
    if composite_quality(lead) < min_composite - 10:
        return False, "low_composite"
    return True, "ok"


def rank_for_delivery(
    leads: list[dict],
    *,
    allow_tier_b: bool = False,
    allow_tier_c: bool = False,
    require_contact: str = "phone_or_email",
    min_composite: int = 55,
    max_deliver: Optional[int] = None,
    prefer_multi_source: bool = True,
) -> list[dict]:
    """
    Filter to contactable high-quality leads, sort best-first, optional cap.
    Non-redundant input assumed (entity resolution already run).
    """
    ranked = []
    for lead in leads:
        ok, reason = is_deliverable(
            lead,
            allow_tier_b=allow_tier_b,
            allow_tier_c=allow_tier_c,
            require_contact=require_contact,
            min_composite=min_composite,
        )
        lead["delivery_ok"] = ok
        lead["delivery_reason"] = reason
        lead["composite_quality"] = composite_quality(lead)
        lead["contactability"], lead["contact_channels"] = contactability(lead)
        if ok:
            ranked.append(lead)

    def sort_key(x: dict):
        multi = len(x.get("sources_hit") or []) if prefer_multi_source else 0
        return (
            multi,
            x.get("composite_quality") or 0,
            x.get("intent_score") or 0,
            x.get("score") or 0,
        )

    ranked.sort(key=sort_key, reverse=True)
    try:
        from app.integrations.prod.source_lanes import pack_with_lane_reserves, annotate_lane
        for L in ranked:
            annotate_lane(L)
        if max_deliver is not None and max_deliver > 0:
            ranked = pack_with_lane_reserves(ranked, int(max_deliver), None)
    except Exception:
        import logging
        logging.getLogger("lead_engine.delivery_ranker").exception("lane packing failed; delivery ranking fails closed")
        return []
    return ranked


def diversity_cap(
    leads: list[dict],
    *,
    max_per_domain: int = 1,
    max_per_name_prefix: int = 2,
) -> list[dict]:
    """
    Avoid delivering 5 branches of the same chain in one batch.
    """
    dom_count: dict[str, int] = {}
    name_count: dict[str, int] = {}
    out = []
    for lead in leads:
        dom = _domain_key_safe(lead)
        prefix = (lead.get("name") or "")[:12].lower()
        if dom:
            if dom_count.get(dom, 0) >= max_per_domain:
                continue
        if prefix and name_count.get(prefix, 0) >= max_per_name_prefix:
            continue
        out.append(lead)
        if dom:
            dom_count[dom] = dom_count.get(dom, 0) + 1
        if prefix:
            name_count[prefix] = name_count.get(prefix, 0) + 1
    return out


def _domain_key_safe(lead: dict) -> Optional[str]:
    try:
        from app.integrations.prod.entity_resolution import _domain_key
        return _domain_key(lead)
    except Exception:
        return None
