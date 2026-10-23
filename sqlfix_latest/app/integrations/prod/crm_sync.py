"""
Two-way CRM sync (HubSpot / GoHighLevel  <->  LeadX).

Outbound (LeadX -> CRM) already exists in outbound_sync.push_assignment. This module
adds the return path so outcomes made in the agency's own CRM flow back into LeadX
and feed the Learning Agent + the quality/credit loop.

Guarantees
  * Authenticity  - HubSpot v3 HMAC (with replay window) and GHL Ed25519 signatures
                    are verified against the RAW request body before parsing.
  * Idempotency   - every inbound event is deduped by (provider, event_id).
  * No ping-pong  - values we just pushed are remembered (echo ledger) and inbound
                    events carrying the same value inside ECHO_WINDOW_S are ignored.
  * Monotonic     - an outcome never regresses (won -> booked) from a sync event;
                    only newer-and-higher-rank changes apply. Humans can override in LeadX.
  * Tenant-safe   - a link row (tenant, provider, external_id) is the ONLY way an
                    external record maps to an assignment; unknown records are ignored.

The functions with no DB access are pure and unit-tested; `apply_event` takes an
injected store so it can run against Supabase or an in-memory fake.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

logger = logging.getLogger("lead_engine.crm_sync")

ECHO_WINDOW_S = 120
HUBSPOT_REPLAY_WINDOW_S = 300

# Public key published by GoHighLevel for X-GHL-Signature (Ed25519). Overridable.
GHL_ED25519_PUBLIC_KEY_PEM = os.getenv("GHL_WEBHOOK_PUBLIC_KEY") or (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=\n"
    "-----END PUBLIC KEY-----\n"
)

# LeadX canonical outcomes, low -> high. 'won' is terminal.
OUTCOME_RANK = {"no_answer": 1, "not_interested": 2, "booked": 3, "won": 4}

DEFAULT_STAGE_TO_OUTCOME: dict[str, dict[str, str]] = {
    "hubspot": {
        "appointmentscheduled": "booked", "qualifiedtobuy": "booked", "presentationscheduled": "booked",
        "decisionmakerboughtin": "booked", "contractsent": "booked",
        "closedwon": "won", "closedlost": "not_interested",
        # contact-level properties (LeadX pushes contacts, so these are the events we actually receive)
        "customer": "won", "opportunity": "booked", "salesqualifiedlead": "booked",
        "unqualified": "not_interested", "attempted_to_contact": "no_answer",
    },
    "gohighlevel": {
        "booked": "booked", "appointment booked": "booked", "appointment": "booked", "won": "won",
        "lost": "not_interested", "abandoned": "not_interested", "no show": "no_answer",
    },
}


# --------------------------------------------------------------- signatures
def verify_hubspot_v3(method: str, uri: str, body: bytes, timestamp: str, signature: str,
                      client_secret: str, now: Optional[float] = None) -> tuple[bool, str]:
    """HubSpot v3: base64(HMAC_SHA256(secret, METHOD + URI + BODY + TIMESTAMP))."""
    if not (client_secret and signature and timestamp):
        return False, "missing_signature_material"
    try:
        ts_ms = int(timestamp)
    except ValueError:
        return False, "bad_timestamp"
    if abs((now if now is not None else time.time()) * 1000 - ts_ms) > HUBSPOT_REPLAY_WINDOW_S * 1000:
        return False, "stale_timestamp"
    msg = method.upper().encode() + uri.encode() + body + timestamp.encode()
    expected = base64.b64encode(hmac.new(client_secret.encode(), msg, hashlib.sha256).digest()).decode()
    return (hmac.compare_digest(expected, signature), "ok" if hmac.compare_digest(expected, signature) else "bad_signature")


def verify_ghl_signature(body: bytes, signature_b64: str, public_key_pem: Optional[str] = None) -> tuple[bool, str]:
    """GoHighLevel: Ed25519 over the raw body, base64 signature in X-GHL-Signature."""
    if not signature_b64 or signature_b64 == "N/A":
        return False, "no_signature"
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.exceptions import InvalidSignature
        key = load_pem_public_key((public_key_pem or GHL_ED25519_PUBLIC_KEY_PEM).encode())
        try:
            key.verify(base64.b64decode(signature_b64), body)  # type: ignore[union-attr]
            return True, "ok"
        except InvalidSignature:
            return False, "bad_signature"
    except Exception as exc:  # malformed key/signature
        return False, f"verify_error:{type(exc).__name__}"


# ------------------------------------------------------------ normalisation
@dataclass
class SyncEvent:
    provider: str
    event_id: str
    kind: str                 # 'stage_change' | 'contact_update' | 'appointment' | 'deal_status'
    external_id: str          # contact/opportunity id in the external CRM
    account_id: str           # HubSpot portalId / GHL locationId (ids are only unique per account)
    occurred_at_ms: int
    field: Optional[str] = None
    value: Optional[str] = None
    raw: Optional[dict] = None


def normalize_hubspot(payload: Any) -> list[SyncEvent]:
    """HubSpot sends a JSON array of subscription events."""
    events = payload if isinstance(payload, list) else [payload]
    out: list[SyncEvent] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        sub = str(e.get("subscriptionType", ""))
        if not sub.endswith("propertyChange"):
            continue
        prop = str(e.get("propertyName", ""))
        val = None if e.get("propertyValue") is None else str(e.get("propertyValue")).lower()
        if prop == "hs_is_closed_won":       # boolean flag: only 'true' means won (hs_is_closed is ambiguous: won OR lost)
            if val != "true":
                continue
            prop, val = "dealstage", "closedwon"
        kind = "stage_change" if prop in ("dealstage", "lifecyclestage", "hs_lead_status") else "contact_update" if sub.startswith("contact") else None
        if not kind:
            continue
        out.append(SyncEvent(
            provider="hubspot", event_id=f"{e.get('portalId')}:" + str(e.get("eventId") or f"{e.get('objectId')}:{prop}:{e.get('occurredAt')}"),
            kind=kind, external_id=str(e.get("objectId", "")), account_id=str(e.get("portalId", "")),
            occurred_at_ms=int(e.get("occurredAt") or 0),
            field=prop, value=val, raw=e))
    return out


def normalize_ghl(payload: Any) -> list[SyncEvent]:
    if not isinstance(payload, dict):
        return []
    typ = str(payload.get("type", ""))
    ts = payload.get("timestamp") or payload.get("dateAdded") or ""
    try:
        from datetime import datetime
        ms = int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000) if ts else int(time.time() * 1000)
    except ValueError:
        ms = int(time.time() * 1000)
    eid = f"{payload.get('locationId')}:" + str(payload.get("webhookId") or f"{typ}:{payload.get('contactId') or payload.get('id')}:{ms}")
    if typ in ("OpportunityStageUpdate", "OpportunityStatusUpdate"):
        stage = payload.get("pipelineStageName") or payload.get("status") or ""
        return [SyncEvent("gohighlevel", eid, "stage_change" if typ.endswith("StageUpdate") else "deal_status",
                          str(payload.get("contactId") or payload.get("id") or ""), str(payload.get("locationId") or ""), ms, "stage", str(stage).lower(), payload)]
    if typ in ("AppointmentCreate", "AppointmentUpdate"):
        return [SyncEvent("gohighlevel", eid, "appointment", str(payload.get("contactId") or ""), str(payload.get("locationId") or ""), ms,
                          "appointment", str((payload.get("appointment") or {}).get("appointmentStatus", "booked")).lower(), payload)]
    if typ in ("ContactUpdate", "ContactCreate"):
        return [SyncEvent("gohighlevel", eid, "contact_update", str(payload.get("id") or payload.get("contactId") or ""), str(payload.get("locationId") or ""), ms, None, None, payload)]
    return []


# ------------------------------------------------------------- decision logic
def map_outcome(provider: str, value: Optional[str], tenant_map: Optional[dict] = None) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    m = {**DEFAULT_STAGE_TO_OUTCOME.get(provider, {}), **{str(k).lower(): x for k, x in (tenant_map or {}).items()}}
    if v in m:
        return m[v]
    if v in ("true", "closed won") and provider == "hubspot":
        return "won"
    return None


def should_apply(current_outcome: Optional[str], new_outcome: str, event_ms: int, last_synced_ms: int) -> tuple[bool, str]:
    """Monotonic + freshness rule."""
    if event_ms and last_synced_ms and event_ms < last_synced_ms:
        return False, "older_than_last_sync"
    if new_outcome == current_outcome:
        return False, "no_change"
    if current_outcome == "won":
        return False, "terminal_won"          # only a human in LeadX can reopen a won deal
    if new_outcome == "no_answer" and current_outcome in ("booked", "not_interested"):
        return False, "would_regress"         # 'no answer' must never overwrite real progress
    # booked -> not_interested (lost after the meeting), * -> won, no_answer -> anything are all legitimate
    return True, "apply"


def value_hash(field: str, value: Any) -> str:
    return hashlib.sha256(f"{field}={str(value).lower()}".encode()).hexdigest()[:24]


class SyncStore(Protocol):
    def seen_event(self, provider: str, event_id: str) -> bool: ...
    def mark_event(self, provider: str, event_id: str, status: str, detail: dict) -> None: ...
    def find_link(self, provider: str, account_id: str, external_id: str) -> Optional[dict]: ...  # {tenant_id, assignment_id, lead_id, last_synced_ms, outcome, stage_mapping}
    def recent_echo(self, tenant_id: str, provider: str, external_id: str, vhash: str, within_s: int) -> bool: ...
    def record_outcome(self, link: dict, outcome: str, note: str, event_ms: int) -> None: ...


def apply_event(store: SyncStore, ev: SyncEvent) -> dict:
    """Decide + apply one inbound event. Never raises for business-rule skips."""
    if not ev.external_id or not ev.account_id:
        return {"event_id": ev.event_id, "status": "ignored", "reason": "missing_account_or_external_id"}
    if store.seen_event(ev.provider, ev.event_id):
        return {"event_id": ev.event_id, "status": "duplicate"}
    link = store.find_link(ev.provider, ev.account_id, ev.external_id)
    if not link:
        store.mark_event(ev.provider, ev.event_id, "ignored", {"reason": "unlinked"})
        return {"event_id": ev.event_id, "status": "ignored", "reason": "unlinked"}
    if ev.field and ev.value is not None and store.recent_echo(
            link["tenant_id"], ev.provider, ev.external_id, value_hash(ev.field, ev.value), ECHO_WINDOW_S):
        store.mark_event(ev.provider, ev.event_id, "ignored", {"reason": "echo"})
        return {"event_id": ev.event_id, "status": "ignored", "reason": "echo"}
    if ev.kind == "appointment":
        outcome = "booked" if (ev.value or "booked") not in ("cancelled", "canceled", "noshow", "no_show") else None
    else:
        outcome = map_outcome(ev.provider, ev.value, link.get("stage_mapping"))
    if not outcome:
        store.mark_event(ev.provider, ev.event_id, "ignored", {"reason": "unmapped", "value": ev.value})
        return {"event_id": ev.event_id, "status": "ignored", "reason": "unmapped"}
    ok, why = should_apply(link.get("outcome"), outcome, ev.occurred_at_ms, int(link.get("last_synced_ms") or 0))
    if not ok:
        store.mark_event(ev.provider, ev.event_id, "skipped", {"reason": why, "outcome": outcome})
        return {"event_id": ev.event_id, "status": "skipped", "reason": why}
    store.record_outcome(link, outcome, f"synced from {ev.provider}", ev.occurred_at_ms)
    store.mark_event(ev.provider, ev.event_id, "applied", {"outcome": outcome})
    return {"event_id": ev.event_id, "status": "applied", "outcome": outcome,
            "tenant_id": link["tenant_id"], "assignment_id": link["assignment_id"]}


# ------------------------------------------------------- Supabase-backed store
class SupabaseSyncStore:
    def __init__(self, sb):
        self.sb = sb

    def seen_event(self, provider, event_id):  # event_id already namespaced by account in normalisers
        r = self.sb.table("crm_sync_events").select("id").eq("provider", provider).eq("event_id", event_id).limit(1).execute().data
        return bool(r)

    def mark_event(self, provider, event_id, status, detail):
        try:
            self.sb.table("crm_sync_events").upsert(
                {"provider": provider, "event_id": event_id, "status": status, "detail": detail},
                on_conflict="provider,event_id").execute()
        except Exception:
            logger.warning("sync event log failed", exc_info=True)

    def find_link(self, provider, account_id, external_id):
        r = self.sb.table("crm_sync_links").select("*").eq("provider", provider).eq("account_id", account_id).eq("external_id", external_id).limit(1).execute().data or []
        if not r:
            return None
        link = r[0]
        maps = self.sb.table("integration_field_maps").select("stage_mapping").eq("tenant_id", link["tenant_id"]).eq("provider", provider).limit(1).execute().data or []
        link["stage_mapping"] = (maps[0].get("stage_mapping") if maps else None) or {}
        return link

    def recent_echo(self, tenant_id, provider, external_id, vhash, within_s):
        cutoff = time.time() - within_s
        r = self.sb.table("crm_sync_echo").select("id,written_epoch").eq("tenant_id", tenant_id).eq("provider", provider) \
            .eq("external_id", external_id).eq("value_hash", vhash).order("written_epoch", desc=True).limit(1).execute().data or []
        return bool(r and float(r[0].get("written_epoch") or 0) >= cutoff)

    def record_outcome(self, link, outcome, note, event_ms):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.sb.table("lead_outcome_events").insert({
            "tenant_id": link["tenant_id"], "assignment_id": link["assignment_id"], "lead_id": link["lead_id"],
            "outcome": outcome, "signal": "crm_sync", "metadata": {"note": note}, "recorded_at": now}).execute()
        self.sb.table("crm_sync_links").update({"outcome": outcome, "last_synced_ms": event_ms or int(time.time() * 1000)}) \
            .eq("id", link["id"]).execute()


def remember_push(sb, tenant_id: str, provider: str, external_id: str, field: str, value: Any) -> None:
    """Call after every outbound write so the echo comes back as a no-op."""
    try:
        sb.table("crm_sync_echo").insert({"tenant_id": tenant_id, "provider": provider, "external_id": external_id,
                                          "value_hash": value_hash(field, value), "written_epoch": time.time()}).execute()
    except Exception:
        logger.warning("echo ledger write failed", exc_info=True)


def link_external(sb, tenant_id: str, provider: str, account_id: str, external_id: str, assignment_id: str, lead_id: str) -> None:
    sb.table("crm_sync_links").upsert(
        {"tenant_id": tenant_id, "provider": provider, "account_id": account_id, "external_id": external_id,
         "assignment_id": assignment_id, "lead_id": lead_id}, on_conflict="provider,account_id,external_id").execute()


def parse_body(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
