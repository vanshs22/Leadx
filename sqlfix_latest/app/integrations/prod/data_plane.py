"""Additive LeadX 16 data-plane adapters.

The legacy tables remain authoritative for compatibility.  These adapters project
successful lifecycle events into the canonical account/contact graph, durable
delivery outbox, verification/replacement projections, and append-only economic
ledger when the corresponding migrations are installed.  Missing optional
objects are treated as a compatibility deployment, never as a reason to issue a
second assignment or call a paid provider.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.memory.supabase_client import get_supabase
from app.services.aio import to_async

logger = logging.getLogger("lead_engine.data_plane")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_text(value: Any, limit: int = 500) -> Optional[str]:
    text = str(value or "").strip()
    return text[:limit] or None


def append_ledger_event(
    *,
    tenant_id: str,
    event_key: str,
    event_type: str,
    direction: str,
    quantity: float,
    unit: str,
    source_type: str,
    source_id: Optional[str] = None,
    job_operation_id: Optional[str] = None,
    provider: Optional[str] = None,
    period: Optional[str] = None,
    assignment_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Append one exact-once economic event through the migration RPC.

    This is intentionally synchronous because provider instrumentation and the
    credit adapter also run in synchronous worker threads.  The RPC itself is
    idempotent on ``tenant_id,event_key``.
    """
    if not tenant_id or not event_key:
        return {"ok": False, "reason": "missing_identity"}
    try:
        result = get_supabase().rpc("leadx_append_economic_ledger_event", {
            "p_tenant_id": str(tenant_id),
            "p_event_key": str(event_key)[:240],
            "p_event_type": str(event_type),
            "p_direction": str(direction),
            "p_quantity": max(0, float(quantity or 0)),
            "p_unit": str(unit),
            "p_source_type": str(source_type),
            "p_source_id": _safe_text(source_id, 240),
            "p_job_operation_id": job_operation_id,
            "p_provider": _safe_text(provider, 120),
            "p_period": period,
            "p_assignment_id": assignment_id,
            "p_metadata": metadata or {},
            "p_occurred_at": _now(),
        }).execute()
        data = getattr(result, "data", result)
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except Exception as exc:
        # Ledger writes are additive during rollout; the legacy usage/credit
        # transaction remains authoritative if this migration is not present.
        logger.debug("economic ledger unavailable: %s", type(exc).__name__)
        return {"ok": False, "reason": "ledger_unavailable"}


append_ledger_event_async = to_async(append_ledger_event)


def enqueue_delivery_outbox(
    *, tenant_id: str, assignment_id: str, lead_id: str,
    destination: str = "portal", provider: Optional[str] = None,
    payload: Optional[dict] = None,
) -> dict:
    """Create one stable assignment delivery event, preserving legacy delivery."""
    normalized_destination = str(destination or "portal").lower()
    # The canonical migration intentionally names CRM/export destinations; the
    # legacy Telegram/email channels still use the direct adapter and are
    # represented as a portal-ready event unless a named canonical destination
    # is configured.
    if normalized_destination not in {"email", "webhook", "salesforce", "hubspot", "pipedrive", "close", "gohighlevel", "export", "portal"}:
        normalized_destination = "portal"
    stable = f"assignment:{assignment_id}:delivery:{normalized_destination}"
    event_payload = {
        "event": "lead.assigned",
        "assignment_id": str(assignment_id),
        "lead_id": str(lead_id),
        "destination": normalized_destination,
        "payload": payload or {},
    }
    row = {
        "tenant_id": tenant_id,
        "event_type": "lead.assigned",
        "aggregate_type": "assignment",
        "aggregate_id": str(assignment_id),
        "payload": event_payload,
        "status": "pending",
        "available_at": _now(),
        "stable_event_id": stable,
        "destination": normalized_destination,
        "provider": _safe_text(provider, 120),
        "payload_hash": _hash(event_payload),
        "outbox_state": "pending",
        "next_attempt_at": _now(),
        "reconciliation_key": stable,
    }
    try:
        result = get_supabase().table("delivery_outbox").upsert(
            row, on_conflict="tenant_id,stable_event_id,destination"
        ).execute()
        rows = getattr(result, "data", None) or []
        return {"ok": True, "stable_event_id": stable, "outbox_id": rows[0].get("id") if rows else None}
    except Exception as exc:
        logger.debug("delivery outbox unavailable: %s", type(exc).__name__)
        return {"ok": False, "reason": "outbox_unavailable", "stable_event_id": stable}


enqueue_delivery_outbox_async = to_async(enqueue_delivery_outbox)


def record_delivery_outcome(
    *, tenant_id: str, assignment_ids: list[str], destination: str,
    ok: bool, error: Optional[str] = None, provider_operation_id: Optional[str] = None,
) -> int:
    """Advance outbox rows after the legacy delivery adapter completes."""
    if not assignment_ids:
        return 0
    sb = get_supabase()
    allowed_destinations = {"email", "webhook", "salesforce", "hubspot", "pipedrive", "close", "gohighlevel", "export", "portal"}
    normalized_destination = str(destination or "portal").lower()
    if normalized_destination not in allowed_destinations:
        normalized_destination = "portal"
    state = "delivered" if ok else "retryable"
    updated = 0
    for assignment_id in assignment_ids:
        stable = f"assignment:{assignment_id}:delivery:{normalized_destination}"
        try:
            rows = (sb.table("delivery_outbox").update({
                "status": "delivered" if ok else "failed",
                "outbox_state": state,
                "delivered_at": _now() if ok else None,
                "safe_error_code": None if ok else "provider_delivery_failed",
                "safe_error_detail": None if ok else _safe_text(error),
            }).eq("tenant_id", tenant_id).eq("stable_event_id", stable).execute().data or [])
            append_ledger_event(
                tenant_id=tenant_id,
                event_key=f"delivery:{stable}:{'ok' if ok else 'failed'}",
                event_type="delivery_outcome",
                direction="informational",
                quantity=1,
                unit="event",
                source_type="delivery_outbox",
                source_id=stable,
                metadata={"destination": destination, "ok": bool(ok), "error_code": "provider_delivery_failed" if not ok else None},
            )
            updated += len(rows)
            if rows:
                outbox_id = rows[0].get("id")
                if outbox_id:
                    attempt = {
                        "tenant_id": tenant_id, "outbox_id": outbox_id,
                        "attempt_number": int(rows[0].get("attempts") or 1),
                        "state": "delivered" if ok else state,
                        "provider_operation_id": provider_operation_id,
                        "safe_error_code": None if ok else "provider_delivery_failed",
                        "safe_error_detail": None if ok else _safe_text(error),
                        "finished_at": _now(),
                    }
                    sb.table("delivery_outbox_attempts").upsert(
                        attempt, on_conflict="outbox_id,attempt_number"
                    ).execute()
        except Exception as exc:
            logger.debug("delivery outcome projection unavailable: %s", type(exc).__name__)
    return updated


record_delivery_outcome_async = to_async(record_delivery_outcome)


def _canonical_account_key(lead: dict, lead_id: Optional[str]) -> str:
    return _hash({
        "fingerprint": lead.get("fingerprint"),
        "domain": lead.get("website_domain") or lead.get("website"),
        "name": str(lead.get("name") or lead.get("company_name") or "").strip().lower(),
        "city": str(lead.get("city") or "").strip().lower(),
        "lead_id": str(lead_id or ""),
    })[:48]


@to_async
def project_canonical_lead(tenant_id: str, lead: dict, lead_id: Optional[str] = None) -> dict:
    """Project an assigned lead into canonical account/contact/evidence tables."""
    if not tenant_id or not isinstance(lead, dict):
        return {"ok": False, "reason": "invalid_input"}
    sb = get_supabase()
    lead_id = str(lead_id or lead.get("id") or "") or None
    now = _now()
    account_key = _canonical_account_key(lead, lead_id)
    account = {
        "tenant_id": tenant_id,
        "source_lead_id": lead_id,
        "canonical_key": account_key,
        "name": str(lead.get("name") or lead.get("company_name") or "Unknown")[:300],
        "normalized_domain": _safe_text(lead.get("website_domain") or lead.get("domain"), 255),
        "website": _safe_text(lead.get("website"), 1000),
        "phone": _safe_text(lead.get("phone_e164") or lead.get("phone_normalized") or lead.get("phone"), 80),
        "field_confidence": lead.get("field_confidence") or {},
        "provenance": {"source": lead.get("source"), "sources": lead.get("sources_hit") or [], "projected_at": now},
        "updated_at": now,
    }
    try:
        account_result = sb.table("canonical_accounts").upsert(
            account, on_conflict="tenant_id,canonical_key"
        ).execute()
        account_rows = getattr(account_result, "data", None) or []
        account_id = account_rows[0].get("id") if account_rows else None
        if not account_id:
            rows = (sb.table("canonical_accounts").select("id").eq("tenant_id", tenant_id).eq("canonical_key", account_key).limit(1).execute().data or [])
            account_id = rows[0].get("id") if rows else None
        if not account_id:
            return {"ok": False, "reason": "account_projection_unreadable"}

        city = _safe_text(lead.get("city"), 120)
        state = _safe_text(lead.get("state"), 80)
        address = _safe_text(lead.get("address"), 500)
        location_key = _hash({"address": address, "city": city, "state": state})[:48]
        sb.table("canonical_locations").upsert({
            "tenant_id": tenant_id, "account_id": account_id, "location_key": location_key,
            "address_line1": address, "city": city, "state": state,
            "postal_code": _safe_text(lead.get("postal_code") or lead.get("zip"), 24),
            "country": _safe_text(lead.get("country") or "US", 80), "is_primary": True,
            "field_confidence": lead.get("field_confidence") or {},
            "provenance": {"source": lead.get("source"), "observed_at": now}, "updated_at": now,
        }, on_conflict="tenant_id,account_id,location_key").execute()

        full_name = _safe_text(lead.get("owner_name") or lead.get("decision_maker_name") or lead.get("contact_name"), 240)
        contact_values = [
            ("email", lead.get("email")),
            ("phone", lead.get("phone_e164") or lead.get("phone_normalized") or lead.get("phone")),
            ("linkedin", lead.get("linkedin_url")),
        ]
        person_id = None
        if full_name or any(value for _, value in contact_values):
            person_key = _hash({"account": account_key, "name": full_name, "contacts": contact_values})[:48]
            names = full_name.split(" ", 1) if full_name else [None, None]
            person_result = sb.table("canonical_people").upsert({
                "tenant_id": tenant_id, "account_id": account_id, "canonical_key": person_key,
                "first_name": _safe_text(names[0] if names else None, 100),
                "last_name": _safe_text(names[1] if len(names) > 1 else None, 160),
                "full_name": full_name, "field_confidence": lead.get("field_confidence") or {},
                "provenance": {"source": lead.get("source"), "projected_at": now}, "updated_at": now,
            }, on_conflict="tenant_id,canonical_key").execute()
            people = getattr(person_result, "data", None) or []
            person_id = people[0].get("id") if people else None
            if not person_id:
                people = (sb.table("canonical_people").select("id").eq("tenant_id", tenant_id).eq("canonical_key", person_key).limit(1).execute().data or [])
                person_id = people[0].get("id") if people else None
            for method_type, value in contact_values:
                normalized = _safe_text(value, 320)
                if not normalized or not person_id:
                    continue
                value_hash = _hash(normalized.lower())
                sb.table("canonical_contact_methods").upsert({
                    "tenant_id": tenant_id, "person_id": person_id, "method_type": method_type,
                    "normalized_value": normalized.lower() if method_type == "email" else normalized,
                    "value_hash": value_hash, "is_primary": True,
                    "field_confidence": float((lead.get("field_confidence") or {}).get(method_type, 0.7) or 0.7),
                    "provenance": {"source": lead.get("source"), "projected_at": now}, "updated_at": now,
                }, on_conflict="tenant_id,method_type,value_hash").execute()

        evidence_subject = {"account_id": account_id} if not person_id else {"person_id": person_id}
        evidence_items = [("name", account.get("name")), ("website", account.get("website")), ("phone", account.get("phone")), ("email", lead.get("email"))]
        for field_name, value in evidence_items:
            if value in (None, "", [], {}):
                continue
            evidence_hash = _hash({"tenant": tenant_id, "subject": evidence_subject, "field": field_name, "value": value, "source": lead.get("source")})
            sb.table("canonical_field_evidence").upsert({
                "tenant_id": tenant_id, **evidence_subject, "field_name": field_name,
                "value": {"value": value}, "source_type": str(lead.get("source") or "pipeline"),
                "source_ref": lead_id, "source_url": _safe_text(lead.get("source_url"), 1000),
                "observed_at": now, "confidence": float((lead.get("field_confidence") or {}).get(field_name, 0.7) or 0.7),
                "evidence_hash": evidence_hash, "is_current": True,
            }, on_conflict="tenant_id,evidence_hash").execute()

        subject_type, subject_id = ("person", person_id) if person_id else ("account", account_id)
        for field_name, value in evidence_items:
            if value in (None, "", [], {}):
                continue
            observed = datetime.now(timezone.utc)
            expires = observed + timedelta(days=14 if field_name in {"email", "phone"} else 30)
            sb.table("canonical_freshness").upsert({
                "tenant_id": tenant_id, "subject_type": subject_type, "subject_id": subject_id,
                "field_name": field_name, "observed_at": observed.isoformat(), "expires_at": expires.isoformat(),
                "freshness_score": 1.0,
            }, on_conflict="tenant_id,subject_type,subject_id,field_name").execute()
        return {"ok": True, "account_id": account_id, "person_id": person_id}
    except Exception as exc:
        logger.debug("canonical projection unavailable: %s", type(exc).__name__)
        return {"ok": False, "reason": "canonical_projection_unavailable"}


@to_async
def record_quality_decision(
    *, tenant_id: str, lead_id: Optional[str], assignment_id: Optional[str],
    decision: str, quality_score: Optional[float], reasons: list[str],
    required_states: Optional[dict] = None, guarantee_version: str = "leadx16-v1",
) -> dict:
    try:
        row = {
            "tenant_id": tenant_id, "lead_id": lead_id, "assignment_id": assignment_id,
            "decision": decision, "quality_score": quality_score,
            "reasons": reasons[:30], "required_states": required_states or {},
            "guarantee_version": guarantee_version, "decided_at": _now(),
        }
        result = get_supabase().table("canonical_quality_decisions").upsert(
            row, on_conflict="tenant_id,assignment_id,guarantee_version"
        ).execute()
        return {"ok": True, "rows": len(getattr(result, "data", None) or [])}
    except Exception:
        return {"ok": False, "reason": "quality_projection_unavailable"}


def request_replacement(
    *, tenant_id: str, assignment_id: str, lead_id: Optional[str], reason: str,
    credit_reason: str, credit_amount: int = 1, detail: Optional[dict] = None,
) -> dict:
    """Persist an idempotent replacement request alongside legacy crediting."""
    key = f"replacement:{assignment_id}:{credit_reason}"
    try:
        result = get_supabase().table("canonical_replacement_requests").upsert({
            "tenant_id": tenant_id, "idempotency_key": key,
            "original_assignment_id": assignment_id, "original_lead_id": lead_id,
            "reason": _safe_text(reason, 500) or "quality_failure",
            "credit_reason": _safe_text(credit_reason, 120) or "quality_failure",
            "status": "credited", "credit_amount": max(0, int(credit_amount)),
            "credit_result": detail or {}, "credited_at": _now(),
        }, on_conflict="tenant_id,idempotency_key").execute()
        append_ledger_event(
            tenant_id=tenant_id, event_key=key, event_type="replacement_credit",
            direction="credit", quantity=max(0, int(credit_amount)), unit="credit",
            source_type="replacement", source_id=assignment_id,
            assignment_id=assignment_id, metadata=detail or {},
        )
        return {"ok": True, "idempotency_key": key, "rows": len(getattr(result, "data", None) or [])}
    except Exception:
        return {"ok": False, "reason": "replacement_projection_unavailable", "idempotency_key": key}


def run_durable_preflight() -> dict:
    """Call the migration preflight RPC without leaking database errors."""
    try:
        result = get_supabase().rpc("leadx_require_durable_preflight", {}).execute()
        data = getattr(result, "data", result)
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except Exception as exc:
        return {"ok": False, "reason": "durable_preflight_failed", "error_code": type(exc).__name__}



def record_outbox_result(*, row: dict, result: dict) -> dict:
    """Persist one adapter outcome, its attempt, and ambiguous reconciliation state."""
    sb = get_supabase()
    outbox_id = row.get("id")
    tenant_id = str(row.get("tenant_id") or "")
    stable_event_id = str(row.get("stable_event_id") or "")
    destination = str(row.get("destination") or row.get("provider") or "portal").lower()
    if not outbox_id or not tenant_id or not stable_event_id:
        return {"ok": False, "reason": "outbox_identity_missing"}
    ok = bool(result.get("ok"))
    retryable = bool(result.get("retryable"))
    ambiguous = bool(result.get("ambiguous"))
    state = "delivered" if ok else "retryable" if retryable else "dead-letter"
    attempt_number = max(1, int(row.get("attempts") or 0) + 1)
    now = _now()
    next_attempt = None
    if retryable:
        delay = result.get("retry_after_seconds")
        if delay is None:
            delay = min(3600, 30 * (2 ** min(attempt_number - 1, 7)))
        next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(delay)))).isoformat()
    patch = {
        "status": "delivered" if ok else "failed",
        "outbox_state": state,
        "next_attempt_at": next_attempt,
        "delivered_at": now if ok else None,
        "safe_error_code": None if ok else _safe_text(result.get("safe_error_code"), 80),
        "safe_error_detail": None if ok else _safe_text(result.get("safe_error_detail"), 300),
        "attempts": attempt_number,
    }
    try:
        sb.table("delivery_outbox").update(patch).eq("id", outbox_id).eq("tenant_id", tenant_id).execute()
        attempt = {
            "tenant_id": tenant_id, "outbox_id": outbox_id, "attempt_number": attempt_number,
            "state": state, "provider_operation_id": _safe_text(result.get("provider_operation_id"), 180),
            "http_status": result.get("status_code"),
            "safe_error_code": None if ok else _safe_text(result.get("safe_error_code"), 80),
            "safe_error_detail": None if ok else _safe_text(result.get("safe_error_detail"), 300),
            "finished_at": now,
        }
        sb.table("delivery_outbox_attempts").upsert(attempt, on_conflict="outbox_id,attempt_number").execute()
        if ambiguous:
            sb.table("delivery_reconciliations").upsert({
                "tenant_id": tenant_id, "outbox_id": outbox_id,
                "stable_event_id": stable_event_id, "destination": destination,
                "external_id": _safe_text(result.get("external_id"), 180),
                "state": "unconfirmed", "safe_detail": _safe_text(result.get("safe_error_detail"), 300),
                "observed_at": now,
            }, on_conflict="tenant_id,stable_event_id,destination").execute()
        return {"ok": True, "outbox_state": state, "attempt_number": attempt_number, "next_attempt_at": next_attempt}
    except Exception as exc:
        logger.debug("delivery outbox result unavailable: %s", type(exc).__name__)
        return {"ok": False, "reason": "outbox_result_unavailable"}


async def process_delivery_outbox_row(row: dict, *, client: Any = None) -> dict:
    """Execute one CRM outbox row without changing assignment status semantics.

    Rows for portal/export/email/webhook remain owned by their existing direct
    delivery paths. CRM rows resolve a tenant connector and never call a provider
    when the connector is absent or disabled.
    """
    from app.integrations.prod.crm_delivery import (
        CRM_PROVIDERS, execute_crm_delivery, make_request, resolve_tenant_connector,
    )
    provider = str(row.get("provider") or row.get("destination") or "").lower()
    if provider not in CRM_PROVIDERS:
        return {"ok": False, "state": "ignored", "reason": "destination_not_crm"}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    assignment_id = str(row.get("aggregate_id") or payload.get("assignment_id") or "")
    lead_id = str(payload.get("lead_id") or "")
    assignment = payload.get("assignment") if isinstance(payload.get("assignment"), dict) else {"provider": provider, "destination": provider}
    lead = payload.get("lead") if isinstance(payload.get("lead"), dict) else None
    mapping = payload.get("mapping") if isinstance(payload.get("mapping"), dict) else {}
    if not lead or not lead_id:
        try:
            from app.services.aio import aexec
            sb = get_supabase()
            assignments = (await aexec(sb.table("lead_assignments").select("id,lead_id,tier,stage_id").eq("id", assignment_id).eq("tenant_id", row.get("tenant_id")).limit(1))).data or []
            if assignments:
                assignment = {**assignment, **assignments[0], "provider": provider, "destination": provider}
                lead_id = str(assignments[0].get("lead_id") or lead_id)
            leads = (await aexec(sb.table("leads_global").select("*").eq("id", lead_id).limit(1))).data or []
            lead = leads[0] if leads else lead
            if not mapping:
                maps = (await aexec(sb.table("integration_field_maps").select("mapping").eq("tenant_id", row.get("tenant_id")).eq("provider", provider).eq("auto_push", True).limit(1))).data or []
                mapping = maps[0].get("mapping") or {} if maps else {}
        except Exception as exc:
            logger.debug("outbox delivery payload lookup unavailable: %s", type(exc).__name__)
    if not isinstance(lead, dict) or not lead_id:
        result = {"ok": False, "retryable": False, "safe_error_code": "assignment_not_found", "safe_error_detail": "assignment payload is unavailable"}
        record_outbox_result(row=row, result=result)
        return result
    resolved = resolve_tenant_connector(str(row.get("tenant_id") or ""), provider, connection_id=payload.get("connection_id"))
    stable_event_id = str(row.get("stable_event_id") or f"assignment:{assignment_id}:delivery:{provider}")
    if resolved.get("state") != "ready":
        result = {"ok": False, "retryable": False, "ambiguous": False, "safe_error_code": resolved.get("reason") or "connector_unconfigured", "safe_error_detail": "CRM connector is not enabled and configured", "provider_operation_id": make_request(tenant_id=str(row.get("tenant_id")), assignment_id=assignment_id, lead_id=lead_id, provider=provider, stable_event_id=stable_event_id, lead=lead, assignment=assignment, mapping=mapping, payload_hash=row.get("payload_hash")).provider_operation_id}
        record_outbox_result(row=row, result=result)
        return result
    request = make_request(
        tenant_id=str(row.get("tenant_id")), assignment_id=assignment_id, lead_id=lead_id,
        provider=provider, stable_event_id=stable_event_id, lead=lead, assignment=assignment,
        mapping=mapping, payload_hash=row.get("payload_hash"),
    )
    result = (await execute_crm_delivery(request, resolved.get("config"), client=client)).as_dict()
    record_outbox_result(row=row, result=result)
    return result


process_delivery_outbox_row_async = process_delivery_outbox_row
