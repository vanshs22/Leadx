"""
Local in-memory data layer — LEADX_MODE=local.

Lets the entire API run with ZERO external accounts (no Supabase, no keys) so
the app can be started, clicked through and tested on a laptop. It implements
the slice of the supabase-py query API that the app actually uses, backed by
JSON files under `.leadx-local/` so data survives a restart.

This is a development/demo convenience, NOT a database:
  - filters are applied in Python, no real query planner
  - no transactions, no constraints, no RLS
Never set LEADX_MODE=local in production; app_service refuses to start that way.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("lead_engine.local_store")

_LOCK = threading.RLock()


def _data_dir() -> Path:
    d = Path(os.getenv("LEADX_LOCAL_DIR", ".leadx-local")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


class _Result:
    def __init__(self, data: Any, count: int | None = None):
        self.data = data
        self.count = count


class _Query:
    """Chainable, in-memory approximation of a supabase-py table query."""

    def __init__(self, store: "LocalStore", table: str):
        self._store = store
        self._table = table
        self._rows: list[dict] = list(store._rows(table))
        self._count = False
        self._single = False

    # ── filters ──────────────────────────────────────────────────────────
    def select(self, *_a: Any, count: Any = None, **_kw: Any) -> "_Query":
        self._count = count is not None
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def neq(self, col: str, val: Any) -> "_Query":
        self._rows = [r for r in self._rows if r.get(col) != val]
        return self

    def in_(self, col: str, vals: list) -> "_Query":
        s = set(vals or [])
        self._rows = [r for r in self._rows if r.get(col) in s]
        return self

    def gt(self, col: str, val: Any) -> "_Query":
        return self._cmp(col, val, lambda a, b: a > b)

    def gte(self, col: str, val: Any) -> "_Query":
        return self._cmp(col, val, lambda a, b: a >= b)

    def lt(self, col: str, val: Any) -> "_Query":
        return self._cmp(col, val, lambda a, b: a < b)

    def lte(self, col: str, val: Any) -> "_Query":
        return self._cmp(col, val, lambda a, b: a <= b)

    def _cmp(self, col, val, op) -> "_Query":
        out = []
        for r in self._rows:
            v = r.get(col)
            if v is None:
                continue
            try:
                if op(v, val):
                    out.append(r)
            except TypeError:
                if op(str(v), str(val)):
                    out.append(r)
        self._rows = out
        return self

    def ilike(self, col: str, pattern: str) -> "_Query":
        needle = (pattern or "").strip("%").lower()
        self._rows = [r for r in self._rows if needle in str(r.get(col) or "").lower()]
        return self

    def like(self, col: str, pattern: str) -> "_Query":
        return self.ilike(col, pattern)

    def contains(self, col: str, vals: list) -> "_Query":
        want = set(vals or [])
        self._rows = [r for r in self._rows if want.issubset(set(r.get(col) or []))]
        return self

    def is_(self, col: str, _val: Any) -> "_Query":
        self._rows = [r for r in self._rows if r.get(col) is None]
        return self

    def or_(self, _expr: str) -> "_Query":
        # Not parsed — returns the unfiltered set rather than silently dropping rows.
        return self

    # ── shaping ──────────────────────────────────────────────────────────
    def order(self, col: str, desc: bool = False, nullsfirst: bool = False, **_kw: Any) -> "_Query":
        self._rows.sort(
            key=lambda r: (r.get(col) is None, _sortable(r.get(col))),
            reverse=bool(desc),
        )
        return self

    def limit(self, n: int) -> "_Query":
        self._rows = self._rows[:n]
        return self

    def range(self, start: int, end: int) -> "_Query":
        self._rows = self._rows[start:end + 1]
        return self

    def single(self) -> "_Query":
        self._single = True
        return self

    maybe_single = single

    # ── writes ───────────────────────────────────────────────────────────
    def insert(self, payload: dict | list) -> "_Query":
        rows = payload if isinstance(payload, list) else [payload]
        added = []
        with _LOCK:
            bucket = self._store._rows(self._table)
            for row in rows:
                row = dict(row)
                row.setdefault("id", self._store._next_id(self._table))
                bucket.append(row)
                added.append(row)
            self._store._flush(self._table)
        self._rows = added
        return self

    def upsert(self, payload: dict | list, on_conflict: str | None = None) -> "_Query":
        rows = payload if isinstance(payload, list) else [payload]
        keys = [k.strip() for k in (on_conflict or "id").split(",") if k.strip()]
        out = []
        with _LOCK:
            bucket = self._store._rows(self._table)
            for row in rows:
                row = dict(row)
                existing = next(
                    (r for r in bucket if all(r.get(k) == row.get(k) for k in keys)),
                    None,
                )
                if existing is not None:
                    existing.update(row)
                    out.append(existing)
                else:
                    row.setdefault("id", self._store._next_id(self._table))
                    bucket.append(row)
                    out.append(row)
            self._store._flush(self._table)
        self._rows = out
        return self

    def update(self, payload: dict) -> "_Query":
        with _LOCK:
            for row in self._rows:
                row.update(payload)
            self._store._flush(self._table)
        return self

    def delete(self) -> "_Query":
        with _LOCK:
            doomed = {id(r) for r in self._rows}
            self._store._data[self._table] = [
                r for r in self._store._rows(self._table) if id(r) not in doomed
            ]
            self._store._flush(self._table)
        return self

    def execute(self) -> _Result:
        if self._single:
            return _Result(self._rows[0] if self._rows else None)
        return _Result(self._rows, count=len(self._rows) if self._count else None)


def _sortable(v: Any):
    """Make mixed/None values orderable so .order() never raises."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


class LocalStore:
    """JSON-file-backed stand-in for a Supabase client."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}
        self._seq: dict[str, int] = {}
        self._dir = _data_dir()
        self._load()
        logger.warning(
            "LEADX_MODE=local — using in-memory/JSON store at %s. "
            "No real database; do not use in production.",
            self._dir,
        )

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> None:
        for f in self._dir.glob("*.json"):
            try:
                self._data[f.stem] = json.loads(f.read_text("utf-8"))
            except Exception as e:
                logger.warning("local_store: could not read %s: %s", f.name, e)

    def _flush(self, table: str) -> None:
        try:
            (self._dir / f"{table}.json").write_text(
                json.dumps(self._data.get(table, []), indent=2, default=str), "utf-8"
            )
        except Exception as e:
            logger.warning("local_store: could not persist %s: %s", table, e)

    def _rows(self, table: str) -> list[dict]:
        return self._data.setdefault(table, [])

    def _next_id(self, table: str) -> str:
        import uuid
        return str(uuid.uuid4())

    # ── supabase-py surface ──────────────────────────────────────────────
    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def from_(self, name: str) -> _Query:
        return self.table(name)

    def rpc(self, fn: str, params: dict | None = None) -> _Result:
        """Emulate the few RPCs the app calls; unknown ones return empty."""
        params = params or {}
        if fn == "tenant_quota_remaining":
            return _Result(999)
        if fn == "seed_default_stages":
            self._seed_stages(params.get("p_tenant_id"))
            return _Result([])
        if fn in ("is_suppressed", "is_opted_out", "check_exclusivity"):
            return _Result(False)
        logger.debug("local_store: unhandled rpc %s — returning empty", fn)
        return _Result([])

    def _seed_stages(self, tenant_id: str | None) -> None:
        if not tenant_id:
            return
        with _LOCK:
            bucket = self._rows("pipeline_stages")
            if any(r.get("tenant_id") == tenant_id for r in bucket):
                return
            defaults = [
                ("New", "new", 1, "#64748b", False, False),
                ("Contacted", "contacted", 2, "#3b82f6", False, False),
                ("Qualified", "qualified", 3, "#8b5cf6", False, False),
                ("Proposal", "proposal", 4, "#f59e0b", False, False),
                ("Won", "won", 5, "#10b981", True, False),
                ("Lost", "lost", 6, "#ef4444", False, True),
            ]
            for name, slug, pos, color, won, lost in defaults:
                bucket.append({
                    "id": self._next_id("pipeline_stages"),
                    "tenant_id": tenant_id,
                    "name": name, "slug": slug, "position": pos, "color": color,
                    "is_won": won, "is_lost": lost,
                })
            self._flush("pipeline_stages")


_store: LocalStore | None = None


def get_local_store() -> LocalStore:
    global _store
    if _store is None:
        _store = LocalStore()
    return _store


def is_local_mode() -> bool:
    return os.getenv("LEADX_MODE", "").strip().lower() == "local"
