"""
Production scoring, quality gate, exclusivity, quota.
"""
from __future__ import annotations

from app.services.aio import aexec, to_async
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.quality")

INDUSTRY_KEYWORDS = {
    "medspa": ["botox", "filler", "laser", "facial", "aesthetics", "microneedling", "hydrafacial", "coolsculpting"],
    "salon": ["hair", "color", "cut", "blowout", "keratin", "nails", "balayage", "highlights"],
    "gym": ["training", "yoga", "pilates", "crossfit", "fitness", "spin", "boxing"],
    "dental": ["dental", "teeth", "whitening", "implant", "orthodont", "veneer"],
    "wellness": ["massage", "chiropractic", "acupuncture", "wellness", "therapy"],
}


def score_lead(lead: dict, industry: str = "medspa", weights: dict | None = None) -> tuple[int, str, str]:
    """Prefer scoring.v3 (MX, weights, freshness); fall back to legacy if import fails."""
    try:
        from app.integrations.prod.scoring import score_lead_v3
        s, t, r, _ = score_lead_v3(lead, industry=industry, weights=weights or {})
        return s, t, r
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    score = 0
    reasons: list[str] = []

    status = (lead.get("business_status") or "OPERATIONAL").upper()
    if status == "CLOSED":
        return 0, "C", "CLOSED — rejected"
    if status == "TEMPORARILY_CLOSED":
        score -= 15
        reasons.append("-15 temporarily closed")

    if lead.get("website") or lead.get("has_website"):
        score += 12
        reasons.append("+12 website")
    else:
        reasons.append("0 no website")

    if lead.get("phone_normalized") or lead.get("phone"):
        score += 12
        reasons.append("+12 phone")
    else:
        reasons.append("0 no phone")

    if lead.get("email") and lead.get("email_valid") is not False:
        score += 22
        reasons.append("+22 valid email")
    elif lead.get("email"):
        score += 8
        reasons.append("+8 email (unverified)")
    else:
        reasons.append("0 no email")

    if lead.get("has_booking") or lead.get("has_online_booking"):
        score += 14
        reasons.append(f"+14 booking ({lead.get('booking_platform') or 'yes'})")
    else:
        score += 4
        reasons.append("+4 no booking (opportunity)")

    rating = float(lead.get("google_rating") or 0)
    reviews = int(lead.get("review_count") or 0)
    if rating >= 4.5 and reviews >= 50:
        score += 14
        reasons.append(f"+14 strong reviews ({rating}★/{reviews})")
    elif rating >= 4.0 and reviews >= 15:
        score += 9
        reasons.append(f"+9 good reviews ({rating}★/{reviews})")
    elif reviews > 0:
        score += 4
        reasons.append(f"+4 some reviews ({reviews})")

    services = [str(s).lower() for s in (lead.get("services") or [])]
    kws = INDUSTRY_KEYWORDS.get(industry.lower().replace(" ", ""), [])
    blob = " ".join(services)
    if any(k in blob for k in kws):
        score += 10
        reasons.append("+10 service fit")

    if lead.get("owner_name"):
        score += 6
        reasons.append("+6 owner name")

    if lead.get("social_links"):
        score += 4
        reasons.append("+4 social")

    score = min(100, max(0, score))
    if score >= 72:
        tier = "A"
    elif score >= 48:
        tier = "B"
    else:
        tier = "C"
    return score, tier, "; ".join(reasons)


def passes_quality_gate(
    lead: dict,
    allow_tier_b: bool = False,
    min_enrichment_fields: int = 1,
    require_contact: str = "phone_or_email",
    allow_tier_c: bool = False,
) -> tuple[bool, str]:
    """
    High-quality gate: contactable + tier + not closed + some enrichment signal.
    Website alone is no longer mandatory (LinkedIn/IG-origin leads allowed if contactable).
    """
    tier = lead.get("tier") or "C"
    allowed = {"A"}
    if allow_tier_b: allowed.add("B")
    if allow_tier_c: allowed.add("C")
    if tier not in allowed:
        return False, f"tier={tier}"

    if (lead.get("business_status") or "").upper() == "CLOSED":
        return False, "closed"

    from app.integrations.prod.contact_state import email_ok as _email_ok, phone_ok as _phone_ok
    # Both predicates validate the supplied value as well as its verification state.
    has_phone = _phone_ok(lead)
    has_email = _email_ok(lead)
    social = lead.get("social_links") if isinstance(lead.get("social_links"), dict) else {}
    has_social = bool(
        lead.get("linkedin_url") or lead.get("instagram_handle")
        or social.get("linkedin") or social.get("instagram")
    )
    if require_contact == "phone" and not has_phone:
        return False, "no_phone"
    elif require_contact == "email" and not has_email:
        return False, "no_email"
    elif require_contact == "phone_and_email" and not (has_phone and has_email):
        return False, "need_phone_and_email"
    elif require_contact in ("phone_or_email_or_social", "any"):
        # Social presence remains useful evidence, but never satisfies the
        # production "verified leads" contact requirement.
        if not (has_phone or has_email):
            return False, "not_contactable"
    elif require_contact == "phone_or_email" and not (has_phone or has_email):
        return False, "not_contactable"

    # Need a real presence signal (site or social or maps address)
    has_presence = bool(
        lead.get("website")
        or lead.get("linkedin_url")
        or lead.get("instagram_handle")
        or lead.get("address")
        or (lead.get("social_links") or {})
    )
    if not has_presence:
        return False, "no_presence"

    fields = 0
    if has_email:
        fields += 1
    if lead.get("has_booking") or lead.get("has_online_booking") or lead.get("booking_platform"):
        fields += 1
    if lead.get("services"):
        fields += 1
    if lead.get("social_links") or lead.get("linkedin_url") or lead.get("instagram_handle"):
        fields += 1
    if lead.get("owner_name") or lead.get("decision_maker_name"):
        fields += 1
    if len(lead.get("sources_hit") or []) >= 2:
        fields += 1
    if fields < min_enrichment_fields:
        return False, f"enrichment_fields={fields}"

    return True, "ok"


def _utc(value: object) -> datetime | None:
    """Parse an optional timestamp without allowing naive local time to leak in."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def shared_pool_admission(
    candidate: dict,
    *,
    now: datetime | None = None,
    reuse_days: int = 14,
    dormant_cooldown_days: int = 30,
    recent_contact_days: int = 90,
) -> dict[str, object]:
    """Return the shared-inventory decision before a candidate enters outreach.

    Stale or dormant inventory remains researchable, but carries a recheck
    requirement. Explicit suppression, closed state, active opt-out, recent
    contact, and an unexpired dormant cooldown are hard blocks.
    """
    current = now or datetime.now(timezone.utc)
    status = str(candidate.get("business_status") or "").strip().upper()
    suppression = str(candidate.get("suppression_status") or candidate.get("contact_status") or "").strip().lower()
    if status in {"CLOSED", "PERMANENTLY_CLOSED"}:
        return {"allowed": False, "recheck_required": False, "reason": "closed"}
    if candidate.get("suppressed") is True or candidate.get("opted_out") is True or candidate.get("global_opt_out") is True:
        return {"allowed": False, "recheck_required": False, "reason": "suppressed_or_opted_out"}
    if suppression in {"suppressed", "opted_out", "do_not_contact", "do_not_mail"}:
        return {"allowed": False, "recheck_required": False, "reason": "suppressed_or_opted_out"}

    contacted = _utc(candidate.get("last_contacted_at"))
    if contacted and contacted >= current - timedelta(days=max(1, int(recent_contact_days))):
        return {"allowed": False, "recheck_required": False, "reason": "recently_contacted"}

    dormant = bool(candidate.get("dormant") or candidate.get("dormant_blocked"))
    dormant_since = _utc(candidate.get("dormant_since"))
    if dormant and dormant_since and current < dormant_since + timedelta(days=max(1, int(dormant_cooldown_days))):
        return {"allowed": False, "recheck_required": True, "reason": "dormant_cooldown"}

    expiry = _utc(candidate.get("freshness_expires_at"))
    verified = max(
        (stamp for stamp in (
            _utc(candidate.get("last_verified_at")),
            _utc(candidate.get("contact_rechecked_at")),
            _utc(candidate.get("rechecked_at")),
        ) if stamp),
        default=None,
    )
    fresh = bool((expiry and expiry > current) or (verified and verified >= current - timedelta(days=max(1, int(reuse_days)))))
    recheck_required = dormant or not fresh
    return {
        "allowed": True,
        "fresh": fresh,
        "dormant": dormant,
        "recheck_required": recheck_required,
        "reason": "recheck_required" if recheck_required else "fresh",
    }


def _norm_offer(value: Optional[str]) -> str:
    """Normalize an explicit competitive lock key.

    Vertical is deliberately not a fallback: a salon can receive offers from
    unrelated sellers. Generic/default labels represent shared delivery and do
    not create an exclusivity lock.
    """
    v = (value or "").strip().lower().replace(" ", "_")
    v = "_".join(part for part in v.split("_") if part)
    if v in {"", "default", "general", "generic", "unknown", "any", "all", "shared", "lead_gen_service", "lead_generation", "service"}:
        return ""
    return v


@to_async
def check_exclusivity_conflict(
    lead_id: str,
    tenant_id: str,
    vertical: str,
    geo: list[str],
    offer_category: Optional[str] = None,
) -> bool:
    """
    True if another *competing* exclusive tenant already holds this lead.

    Lock key = offer_category (seller product type), NOT target vertical alone.

    Example:
      - Tenant A: offer=hair_dryers, target vertical=salon, geo=NJ
      - Tenant B: offer=hair_cream,  target vertical=salon, geo=NJ
      → NO conflict (different products; both can get the same salon)

      - Tenant A: offer=seo_agency, target=salon, geo=NJ
      - Tenant B: offer=seo_agency, target=salon, geo=NJ
      → CONFLICT (same seller type)
    """
    sb = get_supabase()
    my_offer = _norm_offer(offer_category)
    if not my_offer:
        return False

    try:
        r = sb.rpc("check_exclusivity", {
            "p_lead_id": lead_id,
            "p_tenant_id": tenant_id,
            "p_vertical": vertical,
            "p_geo": geo or [],
            "p_offer_category": my_offer,
        }).execute()
        if r.data is not None:
            return bool(r.data)
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Fallback: same offer_category + exclusive + geo overlap
    try:
        assigns = (
            sb.table("lead_assignments")
            .select("tenant_id, icp_id, vertical, offer_category")
            .eq("lead_id", lead_id)
            .neq("tenant_id", tenant_id)
            .not_.in_("status", ["rejected", "expired"])
            .execute()
            .data or []
        )
        for a in assigns:
            other_offer = _norm_offer(a.get("offer_category"))
            icp_id = a.get("icp_id")
            exclusivity = "shared"
            other_geo: set = set()
            if icp_id:
                icp_rows = (
                    sb.table("icp_profiles")
                    .select("vertical, geo, exclusivity, offer_category")
                    .eq("id", icp_id)
                    .limit(1)
                    .execute()
                    .data or []
                )
                if not icp_rows:
                    continue
                exclusivity = (icp_rows[0].get("exclusivity") or "shared").strip().lower()
                other_offer = _norm_offer(icp_rows[0].get("offer_category") or a.get("offer_category"))
                other_geo = set(icp_rows[0].get("geo") or [])
            if exclusivity != "exclusive":
                continue
            if other_offer != my_offer:
                continue  # different product/seller type → share OK
            my_geo = set(geo or [])
            if not other_geo or not my_geo or (other_geo & my_geo):
                return True
    except Exception as e:
        # Paid exclusive delivery must never fail open. A transient DB/RPC
        # problem is safer as a temporary block than leaking an exclusive lead.
        logger.error("exclusivity check unavailable; failing closed: %s", e, exc_info=True)
        return True
    return False


@to_async
def quota_remaining(tenant_id: str) -> int:
    sb = get_supabase()
    rpc_value: int | None = None
    try:
        r = sb.rpc("tenant_quota_remaining", {"p_tenant_id": tenant_id}).execute()
        if r.data is not None:
            rpc_value = max(0, int(r.data))
    except Exception as exc:
        __import__("logging").getLogger(__name__).debug("suppressed optional quota RPC failure: %s", exc)
    # Read the tenant and usage ledger directly as the compatibility authority.
    # Older deployed RPCs can legally return 0 when usage_log has no current
    # period row, even when the tenant has a positive plan limit. Trusting that
    # stale zero caused fresh real tenants to fail before discovery began.
    try:
        t = sb.table("tenants").select("leads_per_month_limit").eq("id", tenant_id).limit(1).execute().data
        if not t:
            return rpc_value or 0
        lim = int(t[0].get("leads_per_month_limit") or 0)
        period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
        try:
            u = (
                sb.table("usage_log")
                .select("leads_delivered, quota_reserved, rollover_credits")
                .eq("tenant_id", tenant_id)
                .eq("period", period)
                .limit(1)
                .execute()
                .data
            )
        except Exception:
            u = (
                sb.table("usage_log")
                .select("leads_delivered, quota_reserved")
                .eq("tenant_id", tenant_id)
                .eq("period", period)
                .limit(1)
                .execute()
                .data
            )
        row = u[0] if u else {}
        used = int(row.get("leads_delivered") or 0)
        reserved = int(row.get("quota_reserved") or 0)
        rollover = int(row.get("rollover_credits") or 0)
        return max(lim + rollover - used - reserved, 0)
    except Exception:
        return rpc_value or 0


async def _tenant_exclusivity_entitled(tenant_id: str) -> bool:
    """Check the commercial entitlement before honoring an exclusive ICP."""
    try:
        from app.core.plans import exclusive_leads_entitled
        rows = (await aexec(
            get_supabase().table("tenants").select("plan,settings").eq("id", tenant_id).limit(1)
        )).data or []
        if not rows:
            return False
        return exclusive_leads_entitled(rows[0].get("plan"), rows[0].get("settings"))
    except Exception as exc:
        logger.error("exclusivity entitlement check unavailable; refusing exclusive assignment: %s", exc)
        return False


async def assign_lead(
    lead_id: str,
    tenant_id: str,
    icp_id: Optional[str],
    vertical: str,
    geo: list[str],
    delivery_channel: str = "telegram",
    tier: str = "A",
    offer_category: Optional[str] = None,
    discovery_request_id: Optional[str] = None,
    run_id: Optional[str] = None,
    lead: Optional[dict] = None,
    assignment_key: Optional[str] = None,
) -> dict:
    """
    Quota + exclusivity + 90-day recency → insert assignment → bump usage.
    Returns {"assigned": bool, "reason": str}.
    """
    sb = get_supabase()
    assignment_key = str(assignment_key or f"{run_id or 'direct'}:{lead_id}")[:240]
    existing_assignment_id: str | None = None

    # The unique (lead_id, tenant_id) assignment is the durable idempotency
    # boundary. Check it before reserving quota so a retry cannot bill twice.
    try:
        # Keep the admission read compatible with deployments before the
        # additive CRM-status migration. The lead/tenant uniqueness and legacy
        # delivery fields are the durable idempotency boundary.
        existing = (await aexec(
            sb.table("lead_assignments").select("id,status,delivered_at,delivery_status")
            .eq("lead_id", str(lead_id)).eq("tenant_id", str(tenant_id)).limit(1)
        )).data or []
        if existing:
            row = existing[0]
            delivered_at = _utc(row.get("delivered_at"))
            if delivered_at and delivered_at >= datetime.now(timezone.utc) - timedelta(days=90):
                return {"assigned": False, "reason": "already_delivered_90d"}
            if delivered_at and delivered_at < datetime.now(timezone.utc) - timedelta(days=90):
                # Historical delivery is eligible again after 90 days. Reuse the
                # unique tenant/lead row instead of creating a duplicate row.
                existing_assignment_id = str(row.get("id") or "") or None
            elif str(row.get("status") or "new") not in {"rejected", "expired"}:
                crm_saved = row.get("crm_status") in (None, "saved", "not_configured")
                return {"assigned": bool(crm_saved), "reason": "idempotent_replay" if crm_saved else "crm_pending", "assignment_id": row.get("id"), "crm_saved": crm_saved, "idempotent": True}
    except Exception as exc:
        logger.error("assignment idempotency check unavailable; refusing assignment: %s", exc, exc_info=True)
        return {"assigned": False, "reason": "assignment_idempotency_unavailable"}

    # Recheck mutable lead state immediately before reservation. Missing
    # optional freshness fields are compatible with legacy rows; explicit stale,
    # suppressed, closed, or invalid contact state always fails closed.
    candidate = dict(lead or {})
    if not candidate:
        try:
            rows = (await aexec(sb.table("leads_global").select("*").eq("id", str(lead_id)).limit(1))).data or []
            candidate = dict(rows[0]) if rows else {}
        except Exception as exc:
            logger.error("assignment lead recheck unavailable: %s", exc, exc_info=True)
            return {"assigned": False, "reason": "lead_recheck_unavailable"}
    if candidate:
        if candidate.get("_shared_pool_candidate"):
            pool_decision = shared_pool_admission(
                candidate,
                reuse_days=int(os.getenv("ENRICHMENT_REUSE_DAYS", "14")),
                dormant_cooldown_days=int(os.getenv("DORMANT_COOLDOWN_DAYS", "30")),
            )
            if not pool_decision.get("allowed"):
                return {"assigned": False, "reason": str(pool_decision.get("reason") or "shared_pool_blocked")}
            if pool_decision.get("recheck_required") and not candidate.get("_shared_pool_rechecked"):
                return {"assigned": False, "reason": "recheck_required"}
        if str(candidate.get("business_status") or "").upper() == "CLOSED":
            return {"assigned": False, "reason": "closed"}
        if candidate.get("suppressed") is True or candidate.get("opted_out") is True or candidate.get("dormant_blocked") is True:
            return {"assigned": False, "reason": "suppressed_or_dormant"}
        expiry = candidate.get("freshness_expires_at")
        if expiry:
            try:
                if datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    return {"assigned": False, "reason": "freshness_expired"}
            except (TypeError, ValueError):
                return {"assigned": False, "reason": "freshness_invalid"}
        from app.integrations.prod.contact_state import has_contact
        if not has_contact(candidate):
            return {"assigned": False, "reason": "no_verified_contact"}

    # 9.0: a run-level atomic slot prevents retries/concurrent workers from
    # delivering more than the explicit "Total leads this run" target.
    run_slot_claimed = False
    if run_id:
        try:
            run_rows = (await aexec(sb.table("pipeline_runs").select("requested_target,target_mode").eq("id", run_id).eq("tenant_id", tenant_id).limit(1))).data or []
            target = int(run_rows[0].get("requested_target") or 0) if run_rows else 0
            if target > 0:
                claimed = (await aexec(sb.rpc("claim_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id, "p_target": target}))).data
                if claimed is not True:
                    return {"assigned": False, "reason": "run_target_exhausted"}
                run_slot_claimed = True
        except Exception as e:
            logger.error("run-level quota guard unavailable; refusing assignment: %s", e, exc_info=True)
            return {"assigned": False, "reason": "run_quota_guard_unavailable"}

    remaining = await quota_remaining(tenant_id)
    if remaining <= 0:
        if run_slot_claimed:
            await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
        return {"assigned": False, "reason": "quota_exhausted"}

    # 90-day recency
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        recent = (
            (await aexec(sb.table("lead_assignments")
            .select("id")
            .eq("lead_id", lead_id)
            .eq("tenant_id", tenant_id)
            .gte("delivered_at", cutoff)
            .limit(1)))
            .data
        )
        if recent:
            if run_slot_claimed:
                await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
            return {"assigned": False, "reason": "already_delivered_90d"}
    except Exception as e:
        if run_slot_claimed:
            await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
        logger.error("recency check unavailable; refusing assignment: %s", e, exc_info=True)
        return {"assigned": False, "reason": "recency_check_unavailable"}

    # Resolve only explicit offer/use-case data. Target vertical is never a
    # competitive lock key; missing or generic offers remain shareable.
    resolved_offer = _norm_offer(offer_category)
    icp_exclusivity = "shared"
    if icp_id:
        try:
            rows = (
                (await aexec(sb.table("icp_profiles")
                .select("tenant_id, vertical, geo, offer_category, exclusivity")
                .eq("id", icp_id)
                .limit(1)))
                .data or []
            )
            if rows:
                icp = rows[0]
                owner = icp.get("tenant_id")
                if owner and str(owner) != str(tenant_id):
                    if run_slot_claimed:
                        await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
                    return {"assigned": False, "reason": "icp_not_authorized"}
                expected_vertical = str(icp.get("vertical") or "").strip().lower()
                if expected_vertical and str(vertical or "").strip().lower() != expected_vertical:
                    if run_slot_claimed:
                        await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
                    return {"assigned": False, "reason": "icp_use_case_mismatch"}
                expected_geo_values = icp.get("geo") or []
                requested_geo_values = geo or []
                if isinstance(expected_geo_values, str):
                    expected_geo_values = [expected_geo_values]
                if isinstance(requested_geo_values, str):
                    requested_geo_values = [requested_geo_values]
                expected_geo = {str(value).strip().lower() for value in expected_geo_values if str(value).strip()}
                requested_geo = {str(value).strip().lower() for value in requested_geo_values if str(value).strip()}
                if expected_geo and requested_geo and not expected_geo.intersection(requested_geo):
                    if run_slot_claimed:
                        await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
                    return {"assigned": False, "reason": "icp_geo_mismatch"}
                resolved_offer = resolved_offer or _norm_offer(icp.get("offer_category"))
                icp_exclusivity = (icp.get("exclusivity") or "shared").strip().lower()
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    if icp_exclusivity == "exclusive" and resolved_offer:
        if not await _tenant_exclusivity_entitled(tenant_id):
            if run_slot_claimed:
                await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
            return {"assigned": False, "reason": "exclusive_not_entitled"}

    if await check_exclusivity_conflict(
        lead_id, tenant_id, vertical, geo, offer_category=resolved_offer or None
    ):
        if run_slot_claimed:
            await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
        return {"assigned": False, "reason": "exclusivity_conflict"}

    # Reserve quota atomically before the assignment write. Approved discovery
    # requests already hold a monthly reservation, so they MUST consume that
    # reserved slot instead of claiming a second generic tenant slot. Direct
    # pipeline assignments continue using the normal tenant reservation.
    try:
        if discovery_request_id:
            reserved = (await aexec(sb.rpc("consume_tenant_discovery_slot", {
                "p_tenant_id": tenant_id,
                "p_request_id": discovery_request_id,
                "p_tier": (tier or "A").upper(),
            }))).data
        else:
            reserved = (await aexec(sb.rpc("reserve_tenant_lead_slot", {
                "p_tenant_id": tenant_id,
                "p_tier": (tier or "A").upper(),
            }))).data
        if reserved is not True:
            if run_slot_claimed:
                await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
            return {"assigned": False, "reason": "quota_exhausted"}
    except Exception as e:
        if run_slot_claimed:
            await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
        logger.error("atomic quota reservation unavailable; refusing assignment: %s", e, exc_info=True)
        return {"assigned": False, "reason": "quota_reservation_unavailable"}

    reserved_quota = True
    try:
        # Every tenant gets pipeline_stages seeded (seed_default_stages / the
        # tenant setup flow) — resolve their "new" stage so an assigned lead
        # actually lands in the New column instead of the unstaged bucket.
        # Without this, stage_id stays NULL and the kanban board (which
        # groups strictly by stage_id) shows every column empty even though
        # leads were delivered.
        new_stage_id = None
        try:
            stage_rows = (
                (await aexec(sb.table("pipeline_stages")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("slug", "new")
                .limit(1)))
                .data
            )
            if stage_rows:
                new_stage_id = stage_rows[0]["id"]
            else:
                # Tenant has no stages yet — seed the defaults, same path
                # the "Seed default stages" button on the pipeline page uses.
                (await aexec(sb.rpc("seed_default_stages", {"p_tenant_id": tenant_id})))
                stage_rows = (
                    (await aexec(sb.table("pipeline_stages")
                    .select("id")
                    .eq("tenant_id", tenant_id)
                    .eq("slug", "new")
                    .limit(1)))
                    .data
                )
                new_stage_id = stage_rows[0]["id"] if stage_rows else None
        except Exception:
            logger.warning("could not resolve New stage for tenant %s", tenant_id, exc_info=True)

        assignment_payload = {
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "icp_id": icp_id,
            "vertical": vertical,
            "offer_category": resolved_offer,
            "geo_tags": geo or [],
            "tier": (tier or "C").upper() if tier else "C",
            "delivered_at": None,
            "delivery_channel": delivery_channel,
            "delivery_status": "pending",
            "status": "new",
            "stage_id": new_stage_id,
            "discovery_request_id": discovery_request_id,
        }
        if existing_assignment_id:
            updated = (await aexec(
                sb.table("lead_assignments").update(assignment_payload)
                .eq("id", existing_assignment_id).eq("tenant_id", tenant_id)
            )).data or []
            if not updated:
                raise RuntimeError("historical_assignment_reuse_failed")
        else:
            await aexec(sb.table("lead_assignments").insert(assignment_payload))
    except Exception as e:
        logger.exception("assign upsert failed")
        if reserved_quota:
            try:
                if discovery_request_id:
                    (await aexec(sb.rpc("release_tenant_discovery_slot", {
                        "p_tenant_id": tenant_id, "p_request_id": discovery_request_id
                    })))
                else:
                    (await aexec(sb.rpc("release_tenant_lead_slot", {
                        "p_tenant_id": tenant_id, "p_tier": (tier or "A").upper()
                    })))
                if run_slot_claimed:
                    (await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id})))
            except Exception:
                logger.critical("quota release failed after assignment error tenant=%s lead=%s", tenant_id, lead_id, exc_info=True)
        return {"assigned": False, "reason": str(e)}

    # Usage was already incremented atomically by reserve_tenant_lead_slot.

    # Put lead on client CRM pipeline board (stage "new")
    try:
        stages = (
            (await aexec(sb.table("pipeline_stages")
            .select("id, slug")
            .eq("tenant_id", tenant_id)
            .order("position")
            .limit(20)))
            .data
            or []
        )
        if not stages:
            try:
                (await aexec(sb.rpc("seed_default_stages", {"p_tenant_id": tenant_id})))
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
            stages = (
                (await aexec(sb.table("pipeline_stages")
                .select("id, slug")
                .eq("tenant_id", tenant_id)
                .order("position")
                .limit(20)))
                .data
                or []
            )
        new_stage = next((s for s in stages if (s.get("slug") or "").lower() in ("new", "lead", "inbox")), None)
        if not new_stage and stages:
            new_stage = stages[0]
        if new_stage:
            (await aexec(sb.table("lead_assignments").update({
                "stage_id": new_stage["id"],
                "stage_slug": new_stage.get("slug") or "new",
            }).eq("lead_id", lead_id).eq("tenant_id", tenant_id)))
    except Exception as e:
        logger.debug("CRM stage seed skipped: %s", e)

    assignment_rows = (await aexec(sb.table("lead_assignments").select("id").eq("lead_id", lead_id).eq("tenant_id", tenant_id).limit(1))).data or []
    assignment_id = assignment_rows[0].get("id") if assignment_rows else None
    crm_projection = {"crm_saved": True, "crm_status": "not_configured"}
    if assignment_id:
        try:
            from app.integrations.prod.crm_projection import project_assignment_to_crm
            crm_projection = await project_assignment_to_crm(str(tenant_id), str(assignment_id), str(lead_id))
        except Exception as exc:
            logger.warning("CRM projection boundary failed tenant=%s assignment=%s: %s", tenant_id, assignment_id, type(exc).__name__)
            crm_projection = {"crm_saved": False, "crm_status": "retryable", "crm_error": "crm_projection_unavailable", "retryable": True}
        # Persist the CRM outcome on the assignment before any delivery worker
        # can treat this row as ready. Retries update the same assignment.
        try:
            await aexec(sb.table("lead_assignments").update({
                "crm_status": "saved" if crm_projection.get("crm_saved") else str(crm_projection.get("crm_status") or "retryable"),
                "crm_company_id": crm_projection.get("company_id"),
                "crm_contact_id": crm_projection.get("contact_id"),
            }).eq("id", str(assignment_id)).eq("tenant_id", str(tenant_id)))
        except Exception as exc:
            logger.debug("CRM assignment status projection skipped: %s", type(exc).__name__)
        if not crm_projection.get("crm_saved"):
            # A CRM-pending assignment is not deliverable and must not consume
            # the run target or tenant quota. Keep the rejected row for audit,
            # release its reservation, and let the target stream replace it.
            try:
                if discovery_request_id:
                    await aexec(sb.rpc("release_tenant_discovery_slot", {
                        "p_tenant_id": tenant_id, "p_request_id": discovery_request_id,
                    }))
                else:
                    await aexec(sb.rpc("release_tenant_lead_slot", {
                        "p_tenant_id": tenant_id, "p_tier": (tier or "A").upper(),
                    }))
                if run_slot_claimed:
                    await aexec(sb.rpc("release_pipeline_run_slot", {"p_run_id": run_id, "p_tenant_id": tenant_id}))
            except Exception as release_exc:
                logger.error("CRM-failed assignment quota release failed: %s", type(release_exc).__name__)
            try:
                await aexec(sb.table("lead_assignments").update({
                    "status": "rejected", "delivery_status": "failed",
                }).eq("id", str(assignment_id)).eq("tenant_id", str(tenant_id)))
            except Exception:
                logger.debug("CRM-failed assignment rejection projection skipped", exc_info=True)
            return {
                "assigned": False,
                "reason": "crm_persistence_failed",
                "assignment_id": assignment_id,
                "crm_saved": False,
                "retryable": bool(crm_projection.get("retryable", True)),
            }
        if not run_id:
            try:
                from app.layers.persistence_layer import record_stage_transition
                stage_run_id = str(f"direct:{assignment_id}")
                await record_stage_transition(
                    str(tenant_id), stage_run_id, str(lead_id), "assignment", "accepted",
                    assignment_id=str(assignment_id), metadata={"assignment_key": assignment_key},
                    supabase=sb,
                )
                await record_stage_transition(
                    str(tenant_id), stage_run_id, str(lead_id), "crm",
                    "accepted" if crm_projection.get("crm_saved") else "retryable",
                    reason=None if crm_projection.get("crm_saved") else str(crm_projection.get("reason") or "crm_projection_failed"),
                    assignment_id=str(assignment_id), metadata={"assignment_key": assignment_key},
                    supabase=sb,
                )
            except Exception as exc:
                logger.debug("stage transition projection skipped: %s", type(exc).__name__)
        try:
            from app.integrations.prod.lead7_runtime import record_assignment
            await record_assignment(str(lead_id), str(tenant_id), str(assignment_id), reason="quota+quality+exclusivity")
        except Exception:
            logger.warning("7.0 assignment audit failed", exc_info=True)
        try:
            from app.integrations.prod.automation_engine import enqueue_event
            enqueue_event(str(tenant_id), "lead.assigned", "assignment", str(assignment_id), {"assignment_id": str(assignment_id), "lead_id": str(lead_id), "tier": (tier or "A").upper()}, f"lead-assigned:{tenant_id}:{assignment_id}")
        except Exception:
            logger.debug("assignment automation event enqueue skipped", exc_info=True)
    try:
        from app.integrations.prod.ops_metrics import bump
        await bump("leads_assigned", 1, tenant_id=str(tenant_id))
    except Exception as exc:
        logger.debug("assignment metric skipped: %s", type(exc).__name__)

    # Additive Task-16 projections: assignment remains the compatibility
    # boundary, while canonical tables/outbox/ledger provide replay-safe
    # downstream state when their migrations are installed.
    try:
        from app.integrations.prod.data_plane import (
            append_ledger_event_async,
            enqueue_delivery_outbox_async,
            project_canonical_lead,
            record_quality_decision,
        )
        lead_rows = (await aexec(
            sb.table("leads_global").select("*").eq("id", lead_id).limit(1)
        )).data or []
        projection_lead = dict(lead_rows[0]) if lead_rows else {"id": lead_id}
        enr_rows = (await aexec(
            sb.table("enrichment").select("*").eq("lead_id", lead_id).limit(1)
        )).data or []
        if enr_rows:
            projection_lead.update(enr_rows[0])
        await project_canonical_lead(str(tenant_id), projection_lead, str(lead_id))
        quality_score = {"A": 85, "B": 70, "C": 45}.get((tier or "C").upper(), 0)
        await record_quality_decision(
            tenant_id=str(tenant_id), lead_id=str(lead_id),
            assignment_id=str(assignment_id), decision="accepted",
            quality_score=quality_score, reasons=["quality_gate", "quota_reserved", "exclusivity_checked"],
            required_states={"contactable": True},
        )
        await enqueue_delivery_outbox_async(
            tenant_id=str(tenant_id), assignment_id=str(assignment_id),
            lead_id=str(lead_id), destination=str(delivery_channel or "portal"),
            provider=str(delivery_channel or "portal"),
            payload={"tier": (tier or "C").upper(), "vertical": vertical},
        )
        await append_ledger_event_async(
            tenant_id=str(tenant_id), event_key=f"assignment:{assignment_id}:consumed",
            event_type="consumed", direction="debit", quantity=1, unit="lead",
            source_type="assignment", source_id=str(assignment_id),
            assignment_id=str(assignment_id), metadata={"tier": (tier or "C").upper(), "run_id": run_id},
        )
    except Exception as exc:
        logger.debug("Task-16 assignment projections skipped: %s", type(exc).__name__)

    return {"assigned": True, "reason": "ok", "assignment_id": assignment_id, "run_id": run_id, **crm_projection}



@to_async
def _bump_usage_fallback(tenant_id: str, tier: str) -> None:
    sb = get_supabase()
    period = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    existing = (
        sb.table("usage_log")
        .select("id, leads_delivered, tier_a_count, tier_b_count, tier_c_count")
        .eq("tenant_id", tenant_id)
        .eq("period", period)
        .limit(1)
        .execute()
        .data
    )
    if existing:
        row = existing[0]
        upd = {
            "leads_delivered": (row.get("leads_delivered") or 0) + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        key = f"tier_{tier.lower()}_count"
        if key in ("tier_a_count", "tier_b_count", "tier_c_count"):
            upd[key] = (row.get(key) or 0) + 1
        sb.table("usage_log").update(upd).eq("id", row["id"]).execute()
    else:
        sb.table("usage_log").insert({
            "tenant_id": tenant_id,
            "period": period,
            "leads_delivered": 1,
            "tier_a_count": 1 if tier == "A" else 0,
            "tier_b_count": 1 if tier == "B" else 0,
            "tier_c_count": 1 if tier == "C" else 0,
        }).execute()
