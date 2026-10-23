"""Strict in-memory Supabase stand-in driven by the real migration schema.
Rejects: unknown tables/columns (insert, update, filter, select), NOT NULL violations,
enum/CHECK violations, bad uuids, bad types, FK violations, unique violations."""
from __future__ import annotations
import re, uuid, copy, json
from datetime import datetime, timezone
from .schema import load_schema

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
# FK targets that are auth.users or not modelled: not enforced
class DBError(Exception):
    def __init__(self, msg): super().__init__(msg); self.message = msg

class Result:
    def __init__(self, data, count=None): self.data, self.count = data, count

class FakeDB:
    def __init__(self, enforce_fk=True):
        self.schema = load_schema(); self.rows: dict[str, list] = {}
        self.enforce_fk = enforce_fk; self.notes: list[str] = []; self.violations: list[str] = []; self.log: list[tuple] = []
        self.skip_fk_tables = {"users", "auth_users"}
    def table(self, name): return Query(self, name.split(".")[-1])
    def _fn_defined(self, name):
        if not hasattr(self, "_fns"):
            from .schema import MIG
            txt = "\n".join(f.read_text() for f in sorted(MIG.glob("*.sql")))
            self._fns = {m.lower() for m in re.findall(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(?:public\.)?(\w+)", txt, re.I)} | \
                        {m.lower() for m in re.findall(r"CREATE\s+FUNCTION\s+(?:public\.)?(\w+)", txt, re.I)}
        return name.lower() in self._fns
    def rpc(self, name, params=None):
        params = params or {}
        if not self._fn_defined(name):
            self.violations.append(f"rpc:{name} is NOT defined in any migration"); raise DBError(f"function {name} does not exist")
        def run():
            if name == "tenant_quota_remaining":
                t = next((r for r in self.rows.get("tenants", []) if r["id"] == params.get("p_tenant_id")), None)
                return Result(max(0, (t or {}).get("leads_per_month_limit", 0) - sum(r.get("leads_delivered", 0) or 0 for r in self.rows.get("usage_log", []) if r.get("tenant_id") == params.get("p_tenant_id"))))
            if name == "claim_pipeline_run_slot":
                self.slots = getattr(self, "slots", {}); k = params["p_run_id"]
                if self.slots.get(k, 0) >= params["p_target"]: return Result(False)
                self.slots[k] = self.slots.get(k, 0) + 1; return Result(True)
            if name == "release_pipeline_run_slot":
                self.slots = getattr(self, "slots", {}); self.slots[params["p_run_id"]] = max(0, self.slots.get(params["p_run_id"], 0) - 1); return Result(True)
            if name in ("reserve_tenant_lead_slot", "consume_tenant_discovery_slot"):
                t = next((r for r in self.rows.get("tenants", []) if r["id"] == params.get("p_tenant_id")), None)
                self.used = getattr(self, "used", {}); u = self.used.get(params["p_tenant_id"], 0)
                if t and u < (t.get("leads_per_month_limit") or 0): self.used[params["p_tenant_id"]] = u + 1; return Result(True)
                return Result(False)
            self.notes.append(f"rpc:{name} not modelled (defined in SQL)"); return Result(None)
        return _Exec(run)
    def fail(self, msg):
        self.violations.append(msg); raise DBError(msg)
    def seed(self, table, row):
        self.rows.setdefault(table, []).append(self._prepare(table, row, "insert", check_fk=False)); return row

    def _tbl(self, name):
        t = self.schema.tables.get(name)
        if not t: self.fail(f'relation "{name}" does not exist')
        return t

    def _coerce_check(self, t, c, v):
        if v is None: return
        ty = c.type
        if ty == "uuid":
            if not isinstance(v, str) or not UUID_RE.match(v): self.fail(f'invalid input syntax for type uuid: "{v}" ({t.name}.{c.name})')
        elif ty in ("jsonb", "json"):
            try: json.dumps(v)
            except Exception: self.fail(f"{t.name}.{c.name}: value not JSON serialisable")
            if isinstance(v, str):
                try: json.loads(v)
                except Exception: self.fail(f"{t.name}.{c.name}: invalid json text {v[:30]!r}")
        elif ty in ("int", "integer", "bigint", "smallint", "serial", "bigserial"):
            if isinstance(v, bool) or not isinstance(v, int):
                if isinstance(v, float) and v.is_integer(): return
                self.fail(f'invalid input syntax for type {ty}: "{v}" ({t.name}.{c.name})')
        elif ty in ("numeric", "real", "double precision", "float", "decimal"):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                self.fail(f'invalid input syntax for type {ty}: "{v}" ({t.name}.{c.name})')
        elif ty == "boolean":
            if not isinstance(v, bool): self.fail(f'invalid input syntax for type boolean: "{v}" ({t.name}.{c.name})')
        elif ty in ("text", "varchar", "citext", "character"):
            if isinstance(v, (dict, list)): self.fail(f"{t.name}.{c.name}: dict/list given for text column")
        elif ty.startswith("timestamp") or ty == "date":
            if not isinstance(v, str): self.fail(f"{t.name}.{c.name}: timestamp must be ISO string, got {type(v).__name__}")
            try: datetime.fromisoformat(v.replace("Z", "+00:00"))
            except Exception: self.fail(f'invalid input syntax for type timestamp: "{v}" ({t.name}.{c.name})')
        elif ty.endswith("[]") or ty == "array":
            if not isinstance(v, list): self.fail(f"{t.name}.{c.name}: array column needs list, got {type(v).__name__}")
        if c.enum is not None and v not in c.enum:
            self.fail(f'new row for relation "{t.name}" violates check constraint: {c.name}={v!r} not in {sorted(c.enum)}')

    def _prepare(self, table, row, op, check_fk=True):
        t = self._tbl(table); out = {}
        for k, v in row.items():
            if k not in t.cols: self.fail(f'column "{k}" of relation "{table}" does not exist')
            self._coerce_check(t, t.cols[k], v); out[k] = copy.deepcopy(v)
        if op == "insert":
            for c in t.cols.values():
                if c.name in out: 
                    if out[c.name] is None and c.notnull and c.default is None: self.fail(f'null value in column "{c.name}" of relation "{table}" violates not-null constraint')
                    if out[c.name] is None and c.notnull and c.default is not None: out[c.name] = self._default(c)
                    continue
                if c.default is not None: out[c.name] = self._default(c)
                elif c.notnull: self.fail(f'null value in column "{c.name}" of relation "{table}" violates not-null constraint')
                else: out[c.name] = None
        if check_fk and self.enforce_fk:
            for k, v in out.items():
                c = t.cols[k]
                if c.fk and v is not None and c.fk[0] not in self.skip_fk_tables and c.fk[0] in self.schema.tables:
                    if not any(r.get(c.fk[1]) == v for r in self.rows.get(c.fk[0], [])):
                        self.fail(f'insert or update on table "{table}" violates foreign key constraint: {k}={v} not in {c.fk[0]}.{c.fk[1]}')
        return out

    def _default(self, c):
        d = (c.default or "").lower()
        if "gen_random_uuid" in d or "uuid_generate" in d: return str(uuid.uuid4())
        if "now()" in d or "current_timestamp" in d: return datetime.now(timezone.utc).isoformat()
        if d in ("generated",): return None
        if d.startswith("'") :
            lit = re.match(r"'(.*?)'", d).group(1)
            if c.type in ("jsonb", "json"):
                try: return json.loads(lit)
                except Exception: return {}
            if c.type.endswith("[]"): return []
            return lit if c.type in ("text","varchar","citext") else lit
        if d in ("true", "false"): return d == "true"
        if re.fullmatch(r"-?\d+(\.\d+)?", d): return float(d) if "." in d else int(d)
        if d.startswith("array") or d == "'{}'": return []
        return None

class _Exec:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()

class _Not:
    def __init__(self, q): self.q = q
    def in_(self, col, vals): return self.q._filter(col, lambda v: v not in set(vals or []))
    def is_(self, col, val): return self.q._filter(col, lambda v: not _is(v, val))
    def eq(self, col, val): return self.q._filter(col, lambda v: v != val)

def _is(v, val): return (v is None) if str(val).lower() == "null" else (v is True if str(val).lower() == "true" else v is False)

class Query:
    def __init__(self, db, table):
        self.db, self.name = db, table; self.t = db._tbl(table)
        self.op, self.payload, self.filters, self._order, self._limit, self._single = "select", None, [], [], None, None
        self._count = False; self._on_conflict = None; self._cols = "*"; self._offset = 0
    @property
    def not_(self): return _Not(self)
    def _chk(self, col):
        c = col.split("->")[0].split(":")[-1].strip()
        if c not in self.t.cols: self.db.fail(f'column {self.name}.{c} does not exist')
    def _filter(self, col, fn, uuidcheck=False):
        self._chk(col); self.filters.append((col, fn)); return self
    def select(self, cols="*", count=None, **kw):
        if self.op == "select" or self.op in ("insert","update","upsert","delete"): self._cols = cols
        if self.op not in ("insert","update","upsert","delete"): self.op = "select"
        self._count = count is not None
        if cols and cols != "*" and "(" not in cols:
            for c in [x.strip() for x in cols.split(",")]:
                c = c.split(":")[-1].strip()
                if c and c != "*" and c not in self.t.cols: self.db.fail(f'column {self.name}.{c} does not exist')
        return self
    def _uuid_val(self, col, val):
        c = self.t.cols.get(col.split("->")[0])
        if c and c.type == "uuid" and val is not None and not (isinstance(val, str) and UUID_RE.match(val)):
            self.db.fail(f'invalid input syntax for type uuid: "{val}" (filter on {self.name}.{col})')
    def eq(self, col, val): self._chk(col); self._uuid_val(col, val); return self._filter(col, lambda v: v == val)
    def neq(self, col, val): self._chk(col); return self._filter(col, lambda v: v != val)
    def in_(self, col, vals):
        self._chk(col); vals = list(vals or [])
        for x in vals: self._uuid_val(col, x)
        return self._filter(col, lambda v: v in vals)
    def is_(self, col, val): return self._filter(col, lambda v: _is(v, val))
    def gt(self, col, val): return self._filter(col, lambda v: v is not None and v > val)
    def gte(self, col, val): return self._filter(col, lambda v: v is not None and v >= val)
    def lt(self, col, val): return self._filter(col, lambda v: v is not None and v < val)
    def lte(self, col, val): return self._filter(col, lambda v: v is not None and v <= val)
    def like(self, col, pat): return self._filter(col, lambda v: re.fullmatch(re.escape(pat).replace("%", ".*"), str(v or "")) is not None)
    def ilike(self, col, pat): return self._filter(col, lambda v: re.fullmatch(re.escape(pat).replace("%", ".*"), str(v or ""), re.I) is not None)
    def contains(self, col, vals):
        return self._filter(col, lambda v: (set(vals) <= set(v or [])) if isinstance(vals, list) else all((v or {}).get(k) == x for k, x in vals.items()))
    def or_(self, expr):
        self.db.violations.append(f"or_({expr}) treated as pass-through"); return self
    def order(self, col, desc=False, **kw): self._chk(col); self._order.append((col, desc)); return self
    def limit(self, n): self._limit = n; return self
    def range(self, a, b): self._offset, self._limit = a, b - a + 1; return self
    def single(self): self._single = "single"; return self
    def maybe_single(self): self._single = "maybe"; return self
    def insert(self, payload, **kw): self.op, self.payload = "insert", payload; return self
    def upsert(self, payload, on_conflict=None, **kw): self.op, self.payload, self._on_conflict = "upsert", payload, on_conflict; return self
    def update(self, payload): self.op, self.payload = "update", payload; return self
    def delete(self): self.op = "delete"; return self

    def _match(self, r): return all(fn(r.get(c.split("->")[0])) for c, fn in self.filters)
    def _unique_conflict(self, row, skip=None):
        rows = self.db.rows.get(self.name, [])
        keys = ([tuple(self.t.pk)] if self.t.pk else []) + list(self.t.unique)
        for key in keys:
            if all(row.get(k) is None for k in key): continue
            for r in rows:
                if r is skip: continue
                if all(r.get(k) == row.get(k) for k in key): return key, r
        return None, None

    def execute(self):
        db = self.db; rows = db.rows.setdefault(self.name, [])
        db.log.append((self.op, self.name))
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            new = [db._prepare(self.name, dict(i), "insert") for i in items]
            for n in new:
                key, _ = self._unique_conflict(n)
                if key: db.fail(f'duplicate key value violates unique constraint on {self.name}{key}')
            rows.extend(new); return self._ret(new)
        if self.op == "upsert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            conflict = tuple(c.strip() for c in self._on_conflict.split(",")) if self._on_conflict else tuple(self.t.pk)
            for c in conflict:
                if c not in self.t.cols: db.fail(f'upsert on_conflict column "{c}" does not exist on {self.name}')
            if self._on_conflict and conflict not in [tuple(self.t.pk)] + list(self.t.unique) and not any(set(conflict) <= set(u) or set(u) <= set(conflict) for u in self.t.unique):
                db.fail(f'there is no unique or exclusion constraint matching the ON CONFLICT specification ({self.name}: {",".join(conflict)})')
            out = []
            for i in items:
                ex = next((r for r in rows if all(r.get(c) == i.get(c) for c in conflict) and all(i.get(c) is not None for c in conflict)), None)
                if ex:
                    patch = db._prepare(self.name, dict(i), "update"); ex.update(patch); out.append(ex)
                else:
                    n = db._prepare(self.name, dict(i), "insert"); rows.append(n); out.append(n)
            return self._ret(out)
        sel = [r for r in rows if self._match(r)]
        if self.op == "update":
            patch = db._prepare(self.name, dict(self.payload), "update")
            for r in sel: r.update(copy.deepcopy(patch))
            return self._ret(sel)
        if self.op == "delete":
            for r in sel: rows.remove(r)
            return self._ret(sel)
        for col, desc in reversed(self._order):
            sel.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0), reverse=desc)
        total = len(sel)
        sel = sel[self._offset:]
        if self._limit is not None: sel = sel[:self._limit]
        return self._ret(sel, total)

    def _ret(self, data, total=None):
        data = copy.deepcopy(data)
        if self._single:
            if len(data) == 1: return Result(data[0], total)
            if self._single == "maybe" and not data: return Result(None, total)
            self.db.fail(f"single(): expected 1 row, got {len(data)} from {self.name}")
        return Result(data, total if total is not None else len(data))
