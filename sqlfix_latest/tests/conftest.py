"""
Shared test fixtures.

Key goal: tests exercise the REAL modules in backend/, never local copies.
To make that possible without a live database, `fake_supabase` provides an
in-memory stand-in for the supabase-py client covering the query surface the
app actually uses (table/select/eq/order/limit/range/insert/update/upsert/
delete/execute/rpc).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Some legacy suites import shared fakes as `from conftest import ...`.
# Pytest may load this file as `tests.conftest`, so expose the same module
# under the compatibility name as well.
sys.modules.setdefault("conftest", sys.modules[__name__])

import pytest

# Import the app package as `backend.*` regardless of where pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Deterministic env so importing app modules never reaches for real services.
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    """Chainable query that filters an in-memory list of dict rows."""

    def __init__(self, store: dict, table: str):
        self._store = store
        self._table = table
        self._rows = list(store.get(table, []))
        self._want_count = False
        self._pending_update = None

    # -- filters -----------------------------------------------------------
    def select(self, *_a, count=None, **_kw):
        self._want_count = count is not None
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def neq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) != val]
        return self

    def in_(self, col, vals):
        vs = set(vals or [])
        self._rows = [r for r in self._rows if r.get(col) in vs]
        return self

    def lte(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") <= val]
        return self

    def lt(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") < val]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") >= val]
        return self

    def ilike(self, col, pattern):
        needle = (pattern or "").strip("%").lower()
        self._rows = [r for r in self._rows if needle in str(r.get(col) or "").lower()]
        return self

    def contains(self, col, vals):
        self._rows = [
            r for r in self._rows
            if set(vals or []).issubset(set(r.get(col) or []))
        ]
        return self

    def is_(self, col, _val):
        self._rows = [r for r in self._rows if r.get(col) is None]
        return self

    # -- shaping -----------------------------------------------------------
    def order(self, col, desc=False, nullsfirst=False, **_kw):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=bool(desc))
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def range(self, start, end):
        self._rows = self._rows[start:end + 1]
        return self

    def single(self):
        return self

    # -- writes ------------------------------------------------------------
    def insert(self, payload):
        rows = payload if isinstance(payload, list) else [payload]
        added = []
        for row in rows:
            row = dict(row)
            row.setdefault("id", f"{self._table}-{len(self._store.setdefault(self._table, [])) + 1}")
            self._store.setdefault(self._table, []).append(row)
            added.append(row)
        self._rows = added
        return self

    def upsert(self, payload, on_conflict=None):
        rows = payload if isinstance(payload, list) else [payload]
        keys = [k.strip() for k in (on_conflict or "id").split(",")]
        out = []
        bucket = self._store.setdefault(self._table, [])
        for row in rows:
            row = dict(row)
            match = next(
                (r for r in bucket if all(r.get(k) == row.get(k) for k in keys)),
                None,
            )
            if match:
                match.update(row)
                out.append(match)
            else:
                row.setdefault("id", f"{self._table}-{len(bucket) + 1}")
                bucket.append(row)
                out.append(row)
        self._rows = out
        return self

    def update(self, payload):
        self._pending_update = dict(payload)
        return self

    def delete(self):
        bucket = self._store.setdefault(self._table, [])
        doomed = {id(r) for r in self._rows}
        self._store[self._table] = [r for r in bucket if id(r) not in doomed]
        return self

    def execute(self):
        if self._pending_update is not None:
            for row in self._rows:
                row.update(self._pending_update)
            self._pending_update = None
        return _Result(self._rows, count=len(self._rows) if self._want_count else None)


class FakeSupabase:
    """Minimal in-memory supabase-py stand-in."""

    def __init__(self, seed: dict | None = None):
        self.store: dict[str, list[dict]] = {k: list(v) for k, v in (seed or {}).items()}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.rpc_returns: dict[str, object] = {}

    def table(self, name: str) -> _Query:
        return _Query(self.store, name)

    def rpc(self, fn: str, params: dict | None = None):
        self.rpc_calls.append((fn, params or {}))
        return _Result(self.rpc_returns.get(fn, []))

    # convenience for assertions
    def rows(self, table: str) -> list[dict]:
        return self.store.get(table, [])


@pytest.fixture
def fake_supabase(monkeypatch):
    """
    Patch get_supabase() everywhere it is imported so real app code runs
    against the in-memory store.
    """
    fake = FakeSupabase()

    import app.memory.supabase_client as sc
    monkeypatch.setattr(sc, "get_supabase", lambda: fake)

    # Modules that did `from ... import get_supabase` hold their own reference.
    for mod in list(sys.modules.values()):
        if mod and getattr(mod, "__name__", "").startswith("backend."):
            if hasattr(mod, "get_supabase"):
                monkeypatch.setattr(mod, "get_supabase", lambda: fake, raising=False)
    return fake


@pytest.fixture
def sample_lead() -> dict:
    """A realistic enriched lead dict as the pipeline would build it."""
    return {
        "name": "Glow Med Spa",
        "company_name": "Glow Med Spa",
        "phone": "(201) 555-0100",
        "phone_normalized": "2015550100",
        "phone_e164": "+12015550100",
        "website": "https://glowmedspa.com",
        "website_domain": "glowmedspa.com",
        "address": "12 Washington St, Hoboken, NJ",
        "city": "Hoboken",
        "business_status": "OPERATIONAL",
        "google_rating": 4.8,
        "review_count": 120,
        "email": "hello@glowmedspa.com",
        "email_valid": True,
        "has_booking": True,
        "booking_platform": "Mindbody",
        "services": ["botox", "filler", "hydrafacial"],
        "social_links": {"instagram": "glowmedspa"},
        "owner_name": "Dana Cruz",
        "enrichment_ok": True,
        "sources_hit": ["maps", "web"],
        "category": "Medical spa",
    }

