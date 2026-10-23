"""Tenant-scoped query helpers.

supabase-py's ``sb.table(name)`` returns a request builder that only has
select/insert/update/upsert/delete -- filters like ``.eq()`` exist on the query
returned by those calls. So the tenant filter is attached AFTER the verb:

    tenant_table(sb, "deals", tid).select("*").limit(10)
    tenant_table(sb, "deals", tid).update({...}).eq("id", x)
    tenant_table(sb, "deals", tid).insert({...})      # tenant_id injected / verified
"""
from __future__ import annotations
from typing import Any

TENANT_COLUMN = "tenant_id"


def require_tenant_id(tenant_id: str) -> str:
    value = str(tenant_id or "").strip()
    if not value:
        raise ValueError("tenant_id_required")
    return value


def _scope_row(row: dict, tid: str) -> dict:
    out = dict(row or {})
    supplied = out.get(TENANT_COLUMN)
    if supplied is not None and str(supplied) != tid:
        raise ValueError("tenant_id_mismatch")
    out[TENANT_COLUMN] = tid
    return out


class ScopedTable:
    def __init__(self, sb: Any, table: str, tenant_id: str):
        self._sb, self._table, self._tid = sb, table, require_tenant_id(tenant_id)

    def _b(self) -> Any:
        return self._sb.table(self._table)

    def select(self, *a: Any, **k: Any) -> Any:
        return self._b().select(*a, **k).eq(TENANT_COLUMN, self._tid)

    def update(self, values: dict, *a: Any, **k: Any) -> Any:
        return self._b().update(_scope_row(values, self._tid), *a, **k).eq(TENANT_COLUMN, self._tid)

    def delete(self, *a: Any, **k: Any) -> Any:
        return self._b().delete(*a, **k).eq(TENANT_COLUMN, self._tid)

    def insert(self, payload: Any, *a: Any, **k: Any) -> Any:
        rows = [_scope_row(r, self._tid) for r in payload] if isinstance(payload, list) else _scope_row(payload, self._tid)
        return self._b().insert(rows, *a, **k)

    def upsert(self, payload: Any, *a: Any, **k: Any) -> Any:
        rows = [_scope_row(r, self._tid) for r in payload] if isinstance(payload, list) else _scope_row(payload, self._tid)
        return self._b().upsert(rows, *a, **k)


def tenant_table(sb: Any, table: str, tenant_id: str) -> ScopedTable:
    return ScopedTable(sb, table, tenant_id)


def tenant_insert(table: str, tenant_id: str, payload: dict) -> dict:
    """Return a row dict with tenant_id forced (kept for existing call sites)."""
    return _scope_row(payload, require_tenant_id(tenant_id))
