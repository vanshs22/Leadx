"""LeadX 7.0 runtime bridge: discovery -> enrichment -> verification ->
intelligence -> gate -> assignment -> delivery.

Unlike the capability/demo API, this module is called by the production
pipeline. It performs real provider calls through the verification mesh and
persists the resulting intelligence when a lead id is available.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any

from app.integrations.prod.contact_verification_mesh import verify_email, verify_phone
from app.integrations.prod.lead_intelligence_6 import evaluate_lead
from app.memory.supabase_client import get_supabase
from app.services.aio import aexec
from app.integrations.prod.lead7_runtime import persist_global_intelligence


def _fresh(ts: Any, ttl_days: int) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() < ttl_days * 86400
    except Exception:
        return False


async def verify_contacts(lead: dict[str, Any]) -> dict[str, Any]:
    """Verify only stale contacts; preserve cached evidence to avoid spend."""
    out = dict(lead)
    observations: list[dict] = []
    now = datetime.now(timezone.utc)

    email = (out.get("email") or "").strip()
    if email and not _fresh(out.get("email_verified_at"), 14):
        result = await verify_email(email)
        observations.extend(result.get("observations") or [])
        c = result.get("canonical") or {}
        out["email_status"] = c.get("status", "unknown")
        out["email_verification_confidence"] = c.get("confidence", 0)
        out["email_verified_at"] = now.isoformat()
        out["email_verification"] = result
        out["email_verified"] = c.get("status") in {"valid", "deliverable"}
        from app.integrations.prod.contact_state import email_level
        out["email_deliverability"] = email_level(out)

    phone = (out.get("phone_e164") or out.get("phone") or "").strip()
    if phone and not _fresh(out.get("phone_verified_at"), 14):
        result = await verify_phone(phone, out.get("country_code"))
        observations.extend(result.get("observations") or [])
        c = result.get("canonical") or {}
        out["phone_status"] = c.get("status", "unknown")
        out["phone_verification_confidence"] = c.get("confidence", 0)
        out["phone_verified_at"] = now.isoformat()
        out["phone_verification"] = result
        out["phone_verified"] = c.get("status") == "valid"

    out["verification_observations"] = observations

    # Paid fallback: if the free/local tiers left this lead without a verified
    # contact, spend provider credits (only for providers whose key is set) to
    # find a better email/phone, then re-verify once. Skipped when
    # LEADX_PAID_FALLBACK=never, or when nothing paid is configured.
    out = await _paid_contact_refill(out, observations)
    return out


def _has_verified_contact(lead: dict[str, Any]) -> bool:
    from app.integrations.prod.contact_state import has_contact
    return has_contact(lead)


async def _paid_contact_refill(out: dict[str, Any], observations: list[dict]) -> dict[str, Any]:
    from app.integrations.prod.paid_policy import paid_fallback_mode
    if paid_fallback_mode() == "never" or _has_verified_contact(out) or out.get("_paid_refill_done"):
        return out
    try:
        from app.integrations.prod.enrichment_mesh import configured_enrichment_providers, enrich_with_paid_connectors
        if not configured_enrichment_providers():
            return out
        out["_paid_refill_done"] = True
        res = await enrich_with_paid_connectors(out, ["email", "phone"], max_providers=int(__import__("os").getenv("LEADX_MAX_REFILL_PROVIDERS", "3")))
    except Exception as exc:
        __import__("logging").getLogger(__name__).warning("paid contact refill skipped: %s", exc)
        return out
    patch = res.get("patch") or {}
    new_email = patch.get("email")
    new_phone = patch.get("phone")
    if not (new_email and new_email != out.get("email")) and not (new_phone and new_phone != out.get("phone")):
        return out
    out = {**out, **{k: v for k, v in patch.items() if k in {"email", "phone", "field_source", "field_confidence"}}}
    out["enrichment_providers_attempted"] = list(dict.fromkeys((out.get("enrichment_providers_attempted") or []) + (res.get("providers_attempted") or [])))
    if new_email:
        out.pop("email_verified_at", None)
    if new_phone:
        out.pop("phone_verified_at", None)
    again = await verify_contacts(out)
    again["verification_observations"] = observations + [o for o in (again.get("verification_observations") or [])]
    return again


async def persist_intelligence(lead: dict[str, Any], tenant_id: str | None = None) -> None:
    """Persist verification, score, gate and state evidence best-effort.

    Pipeline delivery must not fail because an analytics table is temporarily
    unavailable; the lead itself has already been persisted by the normal CRM
    path. Writes are isolated per table so one optional table cannot block the
    core transaction.
    """
    lead_id = lead.get("id")
    if not lead_id:
        return
    sb = get_supabase()
    tid = tenant_id or lead.get("tenant_id")
    obs = lead.get("verification_observations") or []
    if obs:
        rows=[]
        for o in obs:
            rows.append({
                "lead_id": lead_id, "tenant_id": tid,
                "contact_type": "email" if o.get("email") or o.get("provider") in {"hunter","zerobounce","abstract","kickbox","bouncer","neverbounce","self_hosted_email_validator","local_smtp_probe","local_dns_disposable"} else "phone",
                "contact_value": o.get("email") or o.get("phone") or lead.get("email") or lead.get("phone_e164") or lead.get("phone") or "",
                "provider": o.get("provider") or "unknown", "status": o.get("status"),
                "confidence": o.get("confidence"), "metadata": {k:v for k,v in o.items() if k not in {"provider","status","confidence","email","phone"}},
                "verified_at": datetime.now(timezone.utc).isoformat(),
            })
        try: await aexec(sb.table("lead_verification_observations").insert(rows))
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    intel = lead.get("lead_intelligence_6") or {}
    scores = intel.get("scores") or {}
    try:
        await aexec(sb.table("lead_score_snapshots").insert({
            "lead_id": lead_id, "tenant_id": tid, "model_version":"leadx-7.0",
            "dimensions": scores.get("dimensions") or {}, "overall": scores.get("overall") or 0,
            "priority": scores.get("priority"), "reasons": scores.get("reasons") or [],
        }))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    try:
        await aexec(sb.table("lead_quality_gates").upsert({
            "lead_id": lead_id, "tenant_id": tid, "decision": (intel.get("gate") or {}).get("decision","REVIEW"),
            "reasons": (intel.get("gate") or {}).get("reasons") or [], "score": scores.get("overall") or 0,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="lead_id,tenant_id"))
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)


async def prepare_lead(lead: dict[str, Any], *, tenant_id: str | None, industry: str, icp: dict | None = None, reps: list[dict] | None = None) -> dict[str, Any]:
    """Run the real 7.0 post-enrichment intelligence pass."""
    out = await verify_contacts(lead)
    weights = {}
    if tenant_id:
        try:
            from app.integrations.prod.priors import weights_for_tenant
            weights = await weights_for_tenant(tenant_id, industry)
        except Exception:
            weights = {}
    intel = evaluate_lead(out, icp or {"industries":[industry]}, reps or [], weights=weights)
    out["lead_intelligence_6"] = intel
    out["priority_score"] = intel["scores"]["overall"]
    out["lead_value_score"] = intel["lead_value"]
    out["freshness_score"] = intel["freshness"]["freshness_score"]
    out["risk_score"] = intel["scores"]["dimensions"].get("risk", 0)
    out["gate_decision"] = intel["gate"]["decision"]
    out["gate_reasons"] = intel["gate"]["reasons"]
    out["negative_signals"] = intel["negative_signals"]
    out["quality_gate"] = intel["gate"]
    out["delivery_decision"] = intel["delivery"]
    out["lead_state"] = "GATED" if intel["gate"]["eligible"] else "NEEDS_REVIEW" if intel["gate"]["decision"] == "REVIEW" else "REJECTED"
    await persist_intelligence(out, tenant_id)
    # 7.0 canonical wiring: the same intelligence that gates delivery is now
    # written to leads_global so every CRM/API surface reads one source of truth.
    try:
        await persist_global_intelligence(out, tenant_id)
    except Exception:
        # Analytics/canonical enrichment persistence must not turn a provider
        # outage into a lost lead; the pipeline gate still operates on `out`.
        pass
    return out
