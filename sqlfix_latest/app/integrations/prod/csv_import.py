"""
CSV import — a client's existing customer list into their own CRM.

Header-agnostic: maps common column spellings ("Company", "business
name", "Account") onto our fields, so an agency can upload the export
from whatever CRM they're leaving without hand-editing it first.

Imports into companies/contacts/deals (the client's own CRM records),
NOT into leads_global. Delivered leads are our data with exclusivity and
credit rules attached; an imported customer list is the client's. Keeping
them in separate tables is what stops an import from polluting scoring,
suppression, and lead-credit accounting.
"""
from __future__ import annotations

from app.services.aio import to_async
import csv
import io
import logging
import re
from typing import Any, Iterable, Optional

from app.memory.supabase_client import get_supabase

logger = logging.getLogger("lead_engine.csv_import")

MAX_ROWS = 5000

# target field -> accepted header spellings (lowercased, non-alnum stripped)
_COMPANY_MAP = {
    "name": ["company", "companyname", "business", "businessname", "account", "accountname", "organization", "name"],
    "website": ["website", "url", "domain", "web", "site"],
    "phone": ["phone", "phonenumber", "telephone", "tel", "mobile", "businessphone"],
    "email": ["email", "emailaddress", "workemail", "primaryemail"],
    "industry": ["industry", "vertical", "category", "sector", "type"],
    "city": ["city", "town", "locality"],
    "state": ["state", "province", "region"],
    "address": ["address", "street", "streetaddress", "address1"],
    "notes": ["notes", "note", "comments", "description"],
}

_CONTACT_MAP = {
    "full_name": ["name", "fullname", "contact", "contactname", "person"],
    "first_name": ["firstname", "first", "givenname"],
    "last_name": ["lastname", "last", "surname", "familyname"],
    "email": ["email", "emailaddress", "workemail", "primaryemail"],
    "phone": ["phone", "phonenumber", "mobile", "telephone", "tel"],
    "title": ["title", "jobtitle", "role", "position"],
    "company": ["company", "companyname", "account", "business", "organization"],
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def _build_index(headers: Iterable[str], mapping: dict[str, list[str]]) -> dict[str, str]:
    """target field -> actual header present in the file."""
    normed = {_norm(h): h for h in headers if h}
    out: dict[str, str] = {}
    for field, aliases in mapping.items():
        for alias in aliases:
            if alias in normed:
                out[field] = normed[alias]
                break
    return out


def parse_csv(text: str, limit: int = MAX_ROWS) -> tuple[list[dict], list[str]]:
    """(rows, headers). Sniffs the delimiter so tab- and semicolon-
    separated exports work without the user converting them first."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h]
    rows = []
    for i, row in enumerate(reader):
        if i >= limit:
            break
        if any((v or "").strip() for v in row.values()):
            rows.append(row)
    return rows, headers


def preview(text: str, entity: str = "companies", sample: int = 5) -> dict:
    """What the import *will* do, before it does it — column mapping plus
    a few example rows, so a mis-mapped file is caught before 3000 rows
    land in the CRM."""
    rows, headers = parse_csv(text, limit=max(sample, 50))
    mapping = _COMPANY_MAP if entity == "companies" else _CONTACT_MAP
    idx = _build_index(headers, mapping)
    return {
        "ok": bool(idx),
        "entity": entity,
        "headers": headers,
        "mapped": idx,
        "unmapped": [h for h in headers if h not in idx.values()],
        "total_rows": len(rows),
        "sample": rows[:sample],
        "warning": None if idx.get("name") or idx.get("email") else "No company-name or email column detected",
    }


def _clean(v: Any) -> Optional[str]:
    s = str(v or "").strip()
    return s or None


@to_async
def _import_companies(tenant_id: str, rows: list[dict], idx: dict[str, str]) -> dict:
    sb = get_supabase()
    imported = skipped = 0
    errors: list[str] = []
    batch: list[dict] = []
    for r in rows:
        name = _clean(r.get(idx.get("name", "")))
        if not name:
            skipped += 1
            continue
        batch.append(
            {
                "tenant_id": tenant_id,
                "name": name,
                "website": _clean(r.get(idx.get("website", ""))),
                "phone": _clean(r.get(idx.get("phone", ""))),
                "industry": _clean(r.get(idx.get("industry", ""))),
                "city": _clean(r.get(idx.get("city", ""))),
                "state": _clean(r.get(idx.get("state", ""))),
                "address": _clean(r.get(idx.get("address", ""))),
                "source": "csv_import",
            }
        )
    # Chunked so one bad row doesn't cost the whole file.
    for i in range(0, len(batch), 200):
        chunk = batch[i : i + 200]
        try:
            sb.table("companies").insert(chunk).execute()
            imported += len(chunk)
        except Exception as e:
            errors.append(f"rows {i}-{i + len(chunk)}: {str(e)[:200]}")
            for row in chunk:
                try:
                    sb.table("companies").insert(row).execute()
                    imported += 1
                except Exception:
                    skipped += 1
    return {"imported": imported, "skipped": skipped, "errors": errors[:5]}


@to_async
def _import_contacts(tenant_id: str, rows: list[dict], idx: dict[str, str]) -> dict:
    sb = get_supabase()
    # Resolve/create the company each contact belongs to, so imported
    # contacts hang off real company records instead of floating.
    company_ids: dict[str, str] = {}
    try:
        for c in (
            sb.table("companies").select("id, name").eq("tenant_id", tenant_id).limit(5000).execute().data
            or []
        ):
            company_ids[_norm(c["name"])] = c["id"]
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    imported = skipped = 0
    errors: list[str] = []
    for r in rows:
        email = _clean(r.get(idx.get("email", "")))
        full = _clean(r.get(idx.get("full_name", "")))
        if not full:
            first = _clean(r.get(idx.get("first_name", ""))) or ""
            last = _clean(r.get(idx.get("last_name", ""))) or ""
            full = f"{first} {last}".strip() or None
        if not (full or email):
            skipped += 1
            continue

        cname = _clean(r.get(idx.get("company", "")))
        cid = None
        if cname:
            key = _norm(cname)
            cid = company_ids.get(key)
            if not cid:
                try:
                    created = (
                        sb.table("companies")
                        .insert({"tenant_id": tenant_id, "name": cname, "source": "csv_import"})
                        .execute()
                        .data
                    )
                    if created:
                        cid = created[0]["id"]
                        company_ids[key] = cid
                except Exception:
                    cid = None

        try:
            sb.table("contacts").insert(
                {
                    "tenant_id": tenant_id,
                    "company_id": cid,
                    "full_name": full or email,
                    "email": email,
                    "phone": _clean(r.get(idx.get("phone", ""))),
                    "title": _clean(r.get(idx.get("title", ""))),
                    "source": "csv_import",
                }
            ).execute()
            imported += 1
        except Exception as e:
            skipped += 1
            if len(errors) < 5:
                errors.append(str(e)[:200])
    return {"imported": imported, "skipped": skipped, "errors": errors}


async def import_rows(
    tenant_id: str,
    rows: list[dict],
    entity: str = "companies",
    created_by: str = "import",
) -> dict:
    """Queued job body (kind='csv.import'). Rows are already parsed —
    the API parses on upload so it can reject a malformed file
    immediately instead of failing inside a worker minutes later."""
    if not rows:
        return {"ok": False, "error": "no_rows"}
    rows = rows[:MAX_ROWS]
    headers = list(rows[0].keys())
    mapping = _COMPANY_MAP if entity == "companies" else _CONTACT_MAP
    idx = _build_index(headers, mapping)
    if not idx:
        return {"ok": False, "error": "no_recognisable_columns", "headers": headers}

    res = (
        await _import_companies(tenant_id, rows, idx)
        if entity == "companies"
        else await _import_contacts(tenant_id, rows, idx)
    )

    try:
        from app.integrations.prod.notifications import notify_sync

        notify_sync(
            scope="tenant",
            tenant_id=tenant_id,
            kind="import_done",
            title=f"Import finished — {res['imported']} {entity}",
            body=f"{res['skipped']} row(s) skipped." + (" Some batches errored." if res.get("errors") else ""),
            link=f"/{entity}",
            severity="warning" if res.get("errors") else "info",
        )
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)

    return {"ok": True, "entity": entity, "by": created_by, **res}
