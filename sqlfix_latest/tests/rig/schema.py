"""Parse supabase/migrations/*.sql into a column/enum/FK schema (regex based, order-aware)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path

MIG = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

@dataclass
class Col:
    name: str
    type: str
    notnull: bool = False
    default: str | None = None
    enum: set | None = None
    fk: tuple | None = None   # (table, col)
    enum_name: str | None = None

@dataclass
class Table:
    name: str
    cols: dict = field(default_factory=dict)
    pk: list = field(default_factory=list)
    unique: list = field(default_factory=list)   # list of tuples

def _strip_comments(sql):
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)

def _split_top(body):
    parts, depth, cur, q = [], 0, [], False
    for ch in body:
        if ch == "'": q = not q
        if not q:
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if "".join(cur).strip(): parts.append("".join(cur).strip())
    return parts

def _body(sql, start):
    depth, i, q = 0, start, False
    while i < len(sql):
        ch = sql[i]
        if ch == "'": q = not q
        if not q:
            if ch == "(": depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0: return sql[start + 1:i], i
        i += 1
    return sql[start + 1:], len(sql)

_ENUM = re.compile(r"(\w+)\s*(?:::\w+\s*)?(?:=\s*ANY\s*\(\s*ARRAY\s*\[(.*?)\]|IN\s*\((.*?)\))", re.S | re.I)
def _enum_from(expr):
    out = {}
    for m in _ENUM.finditer(expr):
        vals = {v.strip().strip("'") for v in re.findall(r"'([^']*)'", m.group(2) or m.group(3) or "")}
        if vals: out[m.group(1).lower()] = vals
    return out

def _tname(s): return s.replace('"', '').split(".")[-1].lower()

class Schema:
    def __init__(self): self.tables: dict[str, Table] = {}

    def add_col(self, t: Table, spec: str):
        m = re.match(r'"?(\w+)"?\s+([A-Za-z_][\w ]*?(?:\([^)]*\))?(?:\[\])?)(?=\s|$)(.*)', spec, re.S)
        if not m: return
        name, typ, rest = m.group(1).lower(), m.group(2).strip().lower(), m.group(3)
        if name in {"constraint","primary","unique","foreign","check","exclude","like"}: return
        c = t.cols.get(name) or Col(name, typ)
        c.type = typ.split("(")[0].replace("character varying","varchar")
        if re.search(r"\bNOT\s+NULL\b", rest, re.I) or re.search(r"\bPRIMARY\s+KEY\b", rest, re.I): c.notnull = True
        d = re.search(r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT|NULL|CHECK|REFERENCES|PRIMARY|UNIQUE|CONSTRAINT|GENERATED)\b|$)", rest, re.I | re.S)
        if d: c.default = d.group(1).strip()
        if re.search(r"\bGENERATED\b", rest, re.I): c.default = "generated"
        for k, v in _enum_from(rest).items():
            if k == name: c.enum = v; c.enum_name = f"{t.name}_{name}_check"
        fk = re.search(r"REFERENCES\s+([\w\".]+)\s*\(\s*(\w+)\s*\)", rest, re.I)
        if fk: c.fk = (_tname(fk.group(1)), fk.group(2).lower())
        if re.search(r"\bPRIMARY\s+KEY\b", rest, re.I) and name not in t.pk: t.pk.append(name)
        if re.search(r"\bUNIQUE\b", rest, re.I): t.unique.append((name,))
        t.cols[name] = c

    def table_constraint(self, t: Table, spec: str):
        s = re.sub(r"^\s*CONSTRAINT\s+\"?\w+\"?\s+", "", spec, flags=re.I)
        if re.match(r"PRIMARY\s+KEY", s, re.I):
            t.pk = [c.strip().strip('"').lower() for c in re.search(r"\((.*?)\)", s).group(1).split(",")]
        elif re.match(r"UNIQUE", s, re.I):
            m = re.search(r"\((.*?)\)", s)
            if m: t.unique.append(tuple(c.strip().strip('"').lower() for c in m.group(1).split(",")))
        elif re.match(r"CHECK", s, re.I):
            nm = re.match(r"\s*CONSTRAINT\s+\"?(\w+)\"?", spec, re.I)
            for k, v in _enum_from(s).items():
                if k in t.cols:
                    t.cols[k].enum = v; t.cols[k].enum_name = (nm.group(1).lower() if nm else f"{t.name}_{k}_check")
        elif re.match(r"FOREIGN\s+KEY", s, re.I):
            m = re.search(r"FOREIGN\s+KEY\s*\((\w+)\)\s*REFERENCES\s+([\w\".]+)\s*\((\w+)\)", s, re.I)
            if m and m.group(1).lower() in t.cols: t.cols[m.group(1).lower()].fk = (_tname(m.group(2)), m.group(3).lower())

    def load(self):
        for f in sorted(MIG.glob("*.sql")):
            self.apply(_strip_comments(f.read_text()))
        return self

    def apply(self, sql):
        for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\".]+)\s*\(", sql, re.I):
            name = _tname(m.group(1))
            body, _ = _body(sql, m.end() - 1)
            t = self.tables.get(name) or Table(name)
            self.tables.setdefault(name, t)
            for part in _split_top(body):
                if re.match(r"(CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN|EXCLUDE|LIKE)\b", part, re.I): self.table_constraint(t, part)
                else: self.add_col(t, part)
        for m in re.finditer(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([\w\".]+)\s+(.*?);", sql, re.I | re.S):
            name = _tname(m.group(1)); t = self.tables.get(name)
            if not t: continue
            for part in _split_top(m.group(2)):
                a = re.match(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(.*)", part, re.I | re.S)
                if a: self.add_col(t, a.group(1)); continue
                a = re.match(r"ADD\s+(CONSTRAINT\s+.*|CHECK\s*\(.*|FOREIGN.*|UNIQUE.*|PRIMARY.*)", part, re.I | re.S)
                if a: self.table_constraint(t, a.group(1)); continue
                a = re.match(r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?\"?(\w+)\"?", part, re.I)
                if a:
                    nm = a.group(1).lower()
                    for c in t.cols.values():
                        if c.enum is not None and nm in {c.enum_name, f"{t.name}_{c.name}_check"}: c.enum = None
                    continue
                a = re.match(r"ALTER\s+COLUMN\s+\"?(\w+)\"?\s+(?:SET\s+DATA\s+)?TYPE\s+(\w+)", part, re.I)
                if a and a.group(1).lower() in t.cols: t.cols[a.group(1).lower()].type = a.group(2).lower()
                a = re.match(r"ALTER\s+COLUMN\s+\"?(\w+)\"?\s+SET\s+DEFAULT\s+(.*)", part, re.I | re.S)
                if a and a.group(1).lower() in t.cols: t.cols[a.group(1).lower()].default = a.group(2).strip()
                a = re.match(r"ALTER\s+COLUMN\s+\"?(\w+)\"?\s+DROP\s+NOT\s+NULL", part, re.I)
                if a and a.group(1).lower() in t.cols: t.cols[a.group(1).lower()].notnull = False
                a = re.match(r"ALTER\s+COLUMN\s+\"?(\w+)\"?\s+SET\s+NOT\s+NULL", part, re.I)
                if a and a.group(1).lower() in t.cols: t.cols[a.group(1).lower()].notnull = True
        # ADD CONSTRAINT written inside DO/EXECUTE blocks (loop-created) – pick up enum checks too
        for m in re.finditer(r"ALTER\s+TABLE\s+(?:public\.)?(\w+)\s+ADD\s+CONSTRAINT\s+\w+\s+CHECK\s*\((.*?)\)\s*;", sql, re.I | re.S):
            t = self.tables.get(m.group(1).lower())
            if t:
                for k, v in _enum_from(m.group(2)).items():
                    if k in t.cols: t.cols[k].enum = v

_cache = None
def load_schema() -> Schema:
    global _cache
    if _cache is None: _cache = Schema().load()
    return _cache

if __name__ == "__main__":
    s = load_schema()
    print(len(s.tables), "tables")
    for n in ("agent_runs", "pipeline_runs", "leads_global", "lead_assignments", "agent_memory"):
        t = s.tables.get(n)
        print(n, None if not t else (len(t.cols), {c.name: sorted(c.enum) for c in t.cols.values() if c.enum}))
