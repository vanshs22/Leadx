"""
GDPR / DPA data rights — subject access export and right-to-erasure.

Export dumps every row a tenant owns from the CRM tables (schema source of
truth: sql/016_crm_full.sql) so a data-portability request is one call
rather than a manual pile of SQL.

Erasure is anonymise-in-place, not row deletion: contacts are referenced by
deals and activities, so deleting the row would cascade away business
records the tenant is entitled (and often legally required) to keep. The
identifying columns are nulled, free-text rows that quote the person
(notes / activities / tasks) are deleted, and the address is added to the
global opt-out so re-import can't resurrect them.
"""
from __future__ import annotations

from app.services.aio import aexec
import logging
from typing import Any, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.data_rights")

# Tenant-scoped CRM tables, all carrying a tenant_id column.
TENANT_TABLES: tuple[str, ...] = (
    "companies",
    "contacts",
    "deals",
    "deal_stages",
    "activities",
    "tasks",
    "notes",
    "lead_assignments",
)

# Free-text children that may quote the person; removed wholesale on erasure.
_CONTACT_CHILD_TABLES: tuple[str, ...] = ("notes", "activities", "tasks")

# contacts columns that identify a natural person.
_PII_COLUMNS: tuple[str, ...] = (
    "full_name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "mobile",
    "linkedin_url",
    "title",
)

_EXPORT_PAGE = 1000


def _sb():
    return get_supabase()


def export_tenant(tenant_id: str, *, max_rows_per_table: int = 50_000) -> dict[str, Any]:
    """Every row this tenant owns, JSON-serialisable, keyed by table name.

    Paged so a big tenant doesn't hit the PostgREST default row cap
    silently. A table that doesn't exist yet is reported as an error string
    instead of aborting the whole export.
    """
    tid = str(tenant_id)
    out: dict[str, Any] = {"tenant_id": tid, "tables": {}, "counts": {}, "errors": {}}
    for table in TENANT_TABLES:
        rows: list[dict] = []
        try:
            while len(rows) < max_rows_per_table:
                page = (
                    _sb()
                    .table(table)
                    .select("*")
                    .eq("tenant_id", tid)
                    .range(len(rows), len(rows) + _EXPORT_PAGE - 1)
                    .execute()
                    .data
                    or []
                )
                rows.extend(page)
                if len(page) < _EXPORT_PAGE:
                    break
        except Exception as e:  # noqa: BLE001 — partial export beats none
            logger.warning("export %s failed for tenant=%s: %s", table, tid, e)
            out["errors"][table] = str(e)[:200]
        out["tables"][table] = rows
        out["counts"][table] = len(rows)
    return out


async def delete_contact_pii(tenant_id: str, email: str) -> dict[str, Any]:
    """Erase one person from a tenant's CRM and opt them out globally."""
    tid = str(tenant_id)
    em = (email or "").strip().lower()
    if not em:
        return {"ok": False, "error": "email is required"}

    sb = _sb()
    contacts = (
        (await aexec(sb.table("contacts")
        .select("id, full_name, company_id")
        .eq("tenant_id", tid)
        .ilike("email", em)))
        .data
        or []
    )
    contact_ids = [c["id"] for c in contacts]

    deleted: dict[str, int] = {}
    if contact_ids:
        for table in _CONTACT_CHILD_TABLES:
            try:
                gone = (
                    (await aexec(sb.table(table)
                    .delete()
                    .eq("tenant_id", tid)
                    .in_("contact_id", contact_ids)))
                    .data
                    or []
                )
                deleted[table] = len(gone)
            except Exception as e:  # noqa: BLE001
                logger.warning("erasure: %s cleanup failed: %s", table, e)
                deleted[table] = -1

        scrub = {c: None for c in _PII_COLUMNS}
        scrub["status"] = "archived"
        scrub["do_not_contact"] = True
        scrub["custom_fields"] = {}
        try:
            (await aexec(sb.table("contacts").update(scrub).eq("tenant_id", tid).in_(
                "id", contact_ids
            )))
        except Exception as e:  # noqa: BLE001
            logger.error("erasure: contact scrub failed tenant=%s: %s", tid, e)
            return {"ok": False, "error": f"contact scrub failed: {e}"[:300]}

    # Global opt-out last: if the scrub above failed we don't want a
    # half-erased record marked as handled.
    from app.integrations.prod.suppression import add_global_opt_out

    await add_global_opt_out(
        email=em, reason="gdpr_erasure", requested_by=f"data_rights:{tid}"
    )
    return {
        "ok": True,
        "email": em,
        "contacts_anonymized": len(contact_ids),
        "child_rows_deleted": deleted,
        "opted_out": True,
    }


def _demo() -> None:
    """Offline check of the pure bits — no DB."""
    assert "email" in _PII_COLUMNS and "tenant_id" not in _PII_COLUMNS
    assert set(_CONTACT_CHILD_TABLES) <= set(TENANT_TABLES)
    scrub: dict[str, Optional[Any]] = {c: None for c in _PII_COLUMNS}
    assert scrub["email"] is None and len(scrub) == len(_PII_COLUMNS)
    print("data_rights demo ok")


if __name__ == "__main__":
    _demo()
