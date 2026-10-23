"""
Client suppression lists + global opt-out registry.
Filter BEFORE enrichment so suppressed businesses never cost a crawl.
"""
from __future__ import annotations

from app.services.aio import to_async
import logging
from typing import Optional

from app.integrations.prod.utils import fingerprint, normalize_phone, extract_domain
from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.suppression")


def _sb():
    return get_supabase()


def _ids_for_lead(name: str, phone, website, email) -> dict:
    phone_n = normalize_phone(phone)
    domain = extract_domain(website)
    fp = fingerprint(name, phone, website)
    em = (email or "").lower().strip() or None
    return {
        "fingerprint": fp,
        "phone_normalized": phone_n,
        "email": em,
        "domain": domain,
    }


async def is_blocked(
    tenant_id: str,
    *,
    name: str = "",
    phone: Optional[str] = None,
    website: Optional[str] = None,
    email: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Two queries max (one global, one tenant) using in_ filters — not 8 round-trips.
    """
    ids = _ids_for_lead(name, phone, website, email)
    values = [v for v in ids.values() if v]
    if not values:
        return False, ""

    sb = _sb()

    # Global opt-out: single scan via or_ if client supports it; else one query per
    # non-null field collapsed with a local set membership check against fetched rows.
    try:
        opted = await _match_any_global(ids)
        if opted:
            return True, "opt_out"
    except Exception as e:
        logger.warning("opt-out check failed: %s", e)

    try:
        suppressed = await _match_any_suppression(tenant_id, ids)
        if suppressed:
            return True, "suppressed"
    except Exception as e:
        logger.warning("suppression check failed: %s", e)

    return False, ""


@to_async
def _match_any_global(ids: dict) -> bool:
    """1–2 queries: fetch candidates matching any identifier."""
    sb = _sb()
    # Prefer RPC if available
    try:
        r = sb.rpc("is_opted_out", {
            "p_fingerprint": ids.get("fingerprint"),
            "p_phone": ids.get("phone_normalized"),
            "p_email": ids.get("email"),
            "p_domain": ids.get("domain"),
        }).execute()
        if r.data is True or r.data == [True]:
            return True
        if isinstance(r.data, bool):
            return r.data
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    # Fallback: one query per populated field is still bad — batch with OR via PostgREST
    # PostgREST or=(fingerprint.eq.X,phone_normalized.eq.Y,...)
    clauses = []
    for field, val in ids.items():
        if val:
            # escape commas in values
            safe = str(val).replace(",", "")
            clauses.append(f"{field}.eq.{safe}")
    if not clauses:
        return False
    try:
        # supabase-py supports .or_()
        q = sb.table("global_opt_out").select("id").or_(",".join(clauses)).limit(1)
        rows = q.execute().data or []
        return bool(rows)
    except Exception:
        # ultimate fallback: check phone + email only (most common)
        for field in ("phone_normalized", "email", "domain", "fingerprint"):
            val = ids.get(field)
            if not val:
                continue
            rows = (
                sb.table("global_opt_out").select("id").eq(field, val).limit(1).execute().data or []
            )
            if rows:
                return True
        return False


@to_async
def _match_any_suppression(tenant_id: str, ids: dict) -> bool:
    sb = _sb()
    try:
        r = sb.rpc("is_suppressed", {
            "p_tenant_id": tenant_id,
            "p_fingerprint": ids.get("fingerprint"),
            "p_phone": ids.get("phone_normalized"),
            "p_email": ids.get("email"),
            "p_domain": ids.get("domain"),
        }).execute()
        if isinstance(r.data, bool):
            return r.data
        if r.data is True or r.data == [True]:
            return True
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    clauses = []
    for field, val in ids.items():
        if val:
            safe = str(val).replace(",", "")
            clauses.append(f"{field}.eq.{safe}")
    if not clauses:
        return False
    try:
        rows = (
            sb.table("suppression_list")
            .select("id")
            .eq("tenant_id", tenant_id)
            .or_(",".join(clauses))
            .limit(1)
            .execute()
            .data or []
        )
        return bool(rows)
    except Exception:
        for field in ("phone_normalized", "email", "domain", "fingerprint"):
            val = ids.get(field)
            if not val:
                continue
            rows = (
                sb.table("suppression_list")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq(field, val)
                .limit(1)
                .execute()
                .data or []
            )
            if rows:
                return True
        return False


@to_async
def filter_blocked_leads(
    tenant_id: str,
    leads: list[dict],
) -> tuple[list[dict], int, int]:
    """
    Bulk pre-enrich filter.
    Loads tenant suppression + relevant global opt-outs once, matches in memory.
    Returns (kept, suppression_hits, opt_out_hits).
    """
    if not leads:
        return [], 0, 0

    sb = _sb()
    phones, emails, domains, fps = set(), set(), set(), set()
    enriched_ids = []
    for lead in leads:
        name = lead.get("name") or lead.get("company_name") or ""
        ids = _ids_for_lead(
            name,
            lead.get("phone") or lead.get("phone_normalized"),
            lead.get("website"),
            lead.get("email"),
        )
        enriched_ids.append(ids)
        if ids["phone_normalized"]:
            phones.add(ids["phone_normalized"])
        if ids["email"]:
            emails.add(ids["email"])
        if ids["domain"]:
            domains.add(ids["domain"])
        if ids["fingerprint"]:
            fps.add(ids["fingerprint"])

    # Load matching opt-outs in few queries
    opt_phones, opt_emails, opt_domains, opt_fps = set(), set(), set(), set()
    try:
        if phones:
            for row in (
                sb.table("global_opt_out").select("phone_normalized")
                .in_("phone_normalized", list(phones)).execute().data or []
            ):
                if row.get("phone_normalized"):
                    opt_phones.add(row["phone_normalized"])
        if emails:
            for row in (
                sb.table("global_opt_out").select("email")
                .in_("email", list(emails)).execute().data or []
            ):
                if row.get("email"):
                    opt_emails.add(row["email"].lower())
        if domains:
            for row in (
                sb.table("global_opt_out").select("domain")
                .in_("domain", list(domains)).execute().data or []
            ):
                if row.get("domain"):
                    opt_domains.add(row["domain"])
        if fps:
            for row in (
                sb.table("global_opt_out").select("fingerprint")
                .in_("fingerprint", list(fps)).execute().data or []
            ):
                if row.get("fingerprint"):
                    opt_fps.add(row["fingerprint"])
    except Exception as e:
        logger.warning("bulk opt-out load: %s", e)

    # Load tenant suppression similarly
    sup_phones, sup_emails, sup_domains, sup_fps = set(), set(), set(), set()
    try:
        base = sb.table("suppression_list").select(
            "phone_normalized, email, domain, fingerprint"
        ).eq("tenant_id", tenant_id)
        # Pull all for tenant if small, else by in_ batches
        rows = base.limit(10000).execute().data or []
        for row in rows:
            if row.get("phone_normalized"):
                sup_phones.add(row["phone_normalized"])
            if row.get("email"):
                sup_emails.add(row["email"].lower())
            if row.get("domain"):
                sup_domains.add(row["domain"])
            if row.get("fingerprint"):
                sup_fps.add(row["fingerprint"])
    except Exception as e:
        logger.warning("bulk suppression load: %s", e)

    kept = []
    opt_hits = 0
    sup_hits = 0
    for lead, ids in zip(leads, enriched_ids):
        if (
            (ids["phone_normalized"] and ids["phone_normalized"] in opt_phones)
            or (ids["email"] and ids["email"] in opt_emails)
            or (ids["domain"] and ids["domain"] in opt_domains)
            or (ids["fingerprint"] and ids["fingerprint"] in opt_fps)
        ):
            opt_hits += 1
            lead["_blocked"] = "opt_out"
            continue
        if (
            (ids["phone_normalized"] and ids["phone_normalized"] in sup_phones)
            or (ids["email"] and ids["email"] in sup_emails)
            or (ids["domain"] and ids["domain"] in sup_domains)
            or (ids["fingerprint"] and ids["fingerprint"] in sup_fps)
        ):
            sup_hits += 1
            lead["_blocked"] = "suppressed"
            continue
        kept.append(lead)

    return kept, sup_hits, opt_hits


@to_async
def upload_suppression_rows(
    tenant_id: str,
    rows: list[dict],
    source: str = "upload",
) -> dict:
    sb = _sb()
    inserted = 0
    batch = []
    for r in rows:
        phone = normalize_phone(r.get("phone") or r.get("Phone") or r.get("phone_number"))
        email = (r.get("email") or r.get("Email") or "").strip().lower() or None
        website = r.get("website") or r.get("Website") or r.get("domain")
        name = r.get("company_name") or r.get("Company") or r.get("name") or ""
        domain = extract_domain(website) if website else (
            email.split("@")[-1] if email and "@" in email else None
        )
        fp = fingerprint(name, phone, website)
        batch.append({
            "tenant_id": tenant_id,
            "fingerprint": fp,
            "phone_normalized": phone,
            "email": email,
            "domain": domain,
            "company_name": name or None,
            "source": source,
        })
        if len(batch) >= 100:
            sb.table("suppression_list").insert(batch).execute()
            inserted += len(batch)
            batch = []
    if batch:
        sb.table("suppression_list").insert(batch).execute()
        inserted += len(batch)
    return {"inserted": inserted}


@to_async
def add_global_opt_out(
    *,
    name: str = "",
    phone: Optional[str] = None,
    email: Optional[str] = None,
    website: Optional[str] = None,
    reason: str = "requested",
    requested_by: str = "admin",
) -> dict:
    sb = _sb()
    phone_n = normalize_phone(phone)
    domain = extract_domain(website)
    fp = fingerprint(name, phone, website)
    row = {
        "fingerprint": fp,
        "phone_normalized": phone_n,
        "email": (email or "").lower() or None,
        "domain": domain,
        "company_name": name or None,
        "reason": reason,
        "requested_by": requested_by,
    }
    try:
        sb.table("global_opt_out").insert(row).execute()
    except Exception as e:
        logger.info("opt-out insert: %s", e)
    return {"ok": True, **{k: v for k, v in row.items() if v}}
