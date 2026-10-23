"""Idempotent assignment -> tenant CRM company/contact projection.

The lead engine's global pool and tenant assignments remain canonical. This
module only projects an assigned lead into the existing CRM tables so a lead
that appears in the portal also has durable company/contact provenance.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.aio import aexec
from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.crm_projection")


def _value(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


async def project_assignment_to_crm(tenant_id: str, assignment_id: str, lead_id: str) -> dict[str, Any]:
    """Project one assignment into existing tenant CRM tables, replay-safely."""
    sb = get_supabase()
    try:
        lead_rows = (await aexec(
            sb.table("leads_global").select("*").eq("id", str(lead_id)).limit(1)
        )).data or []
        assignment_rows = (await aexec(
            sb.table("lead_assignments").select("*").eq("id", str(assignment_id)).eq("tenant_id", str(tenant_id)).limit(1)
        )).data or []
        if not lead_rows or not assignment_rows:
            return {"crm_saved": False, "status": "failed", "reason": "source_record_missing", "retryable": False}
        lead = dict(lead_rows[0])
        assignment = dict(assignment_rows[0])
        enrichment_rows = (await aexec(
            sb.table("enrichment").select("*").eq("lead_id", str(lead_id)).limit(1)
        )).data or []
        enrichment = dict(enrichment_rows[0]) if enrichment_rows else {}

        company_rows = (await aexec(
            sb.table("companies").select("*").eq("tenant_id", str(tenant_id)).eq("source_lead_id", str(lead_id)).limit(1)
        )).data or []
        company = company_rows[0] if company_rows else None
        if not company:
            company_payload = {
                "tenant_id": str(tenant_id),
                "name": _value(lead.get("name"), lead.get("company_name"), "Untitled account"),
                "domain": _value(lead.get("website_domain"), enrichment.get("website_domain")),
                "website": _value(lead.get("website"), enrichment.get("website")),
                "phone": _value(lead.get("phone_e164"), lead.get("phone_normalized"), enrichment.get("phone")),
                "address": lead.get("address"),
                "city": lead.get("city"),
                "state": lead.get("state"),
                "country": lead.get("country"),
                "industry": _value(lead.get("category"), lead.get("industry"), assignment.get("vertical")),
                "employee_count": _value(lead.get("employee_count"), enrichment.get("employee_count")),
                "status": "prospect",
                "tags": ["leadx", "assigned"],
                "source": _value(lead.get("source"), "lead_engine"),
                "source_lead_id": str(lead_id),
            }
            try:
                company_rows = (await aexec(sb.table("companies").insert(company_payload))).data or []
                company = company_rows[0] if company_rows else company_payload
            except Exception:
                # A concurrent retry may have won the source_lead/domain race.
                company_rows = (await aexec(
                    sb.table("companies").select("*").eq("tenant_id", str(tenant_id)).eq("source_lead_id", str(lead_id)).limit(1)
                )).data or []
                if not company_rows:
                    return {"crm_saved": False, "status": "retryable", "reason": "company_persistence_failed", "retryable": True}
                company = company_rows[0]

        company_id = str(company.get("id")) if company.get("id") else None
        contact_candidate = _value(
            enrichment.get("email"), enrichment.get("phone"), lead.get("email"),
            lead.get("phone_e164"), lead.get("phone_normalized"),
            enrichment.get("owner_name"), enrichment.get("decision_maker_name"),
        )
        contact = None
        if company_id and contact_candidate:
            contact_rows = (await aexec(
                sb.table("contacts").select("*").eq("tenant_id", str(tenant_id)).eq("source_lead_id", str(lead_id)).limit(1)
            )).data or []
            contact = contact_rows[0] if contact_rows else None
            if not contact:
                contact_payload = {
                    "tenant_id": str(tenant_id),
                    "company_id": company_id,
                    "full_name": _value(enrichment.get("decision_maker_name"), enrichment.get("owner_name")),
                    "title": _value(enrichment.get("decision_maker_title"), enrichment.get("owner_title")),
                    "email": _value(enrichment.get("email"), lead.get("email")),
                    "phone": _value(enrichment.get("phone"), lead.get("phone_e164"), lead.get("phone_normalized")),
                    "linkedin_url": _value(enrichment.get("linkedin_url"), lead.get("linkedin_url")),
                    "is_primary": True,
                    "is_decision_maker": bool(_value(enrichment.get("decision_maker_name"), enrichment.get("owner_name"))),
                    "status": "active",
                    "source": _value(lead.get("source"), "lead_engine"),
                    "source_lead_id": str(lead_id),
                }
                try:
                    contact_rows = (await aexec(sb.table("contacts").insert(contact_payload))).data or []
                    contact = contact_rows[0] if contact_rows else contact_payload
                except Exception:
                    contact_rows = (await aexec(
                        sb.table("contacts").select("*").eq("tenant_id", str(tenant_id)).eq("source_lead_id", str(lead_id)).limit(1)
                    )).data or []
                    if contact_rows:
                        contact = contact_rows[0]
                    else:
                        return {"crm_saved": False, "status": "retryable", "reason": "contact_persistence_failed", "retryable": True, "company_id": company_id}

        contact_id = str(contact.get("id")) if contact and contact.get("id") else None
        if not contact_candidate:
            return {
                "crm_saved": False, "status": "partial", "reason": "contact_evidence_missing",
                "retryable": True, "company_id": company_id, "contact_id": None,
            }
        return {
            "crm_saved": bool(company_id and contact_id), "status": "saved" if company_id and contact_id else "partial",
            "reason": "ok" if company_id and contact_id else "crm_projection_incomplete",
            "retryable": not bool(company_id and contact_id), "company_id": company_id, "contact_id": contact_id,
        }
    except Exception as exc:
        logger.warning("CRM projection failed tenant=%s assignment=%s: %s", tenant_id, assignment_id, type(exc).__name__)
        return {"crm_saved": False, "status": "retryable", "reason": "crm_projection_unavailable", "retryable": True}
