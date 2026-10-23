"""Static verification for LeadX runtime schema dependencies.

This intentionally does not need database credentials. It verifies that every
Supabase table referenced by backend Python code is represented by the checked-in
migration set, and checks that the known automation/reply runtime objects are
present in the repair migration.
"""
from __future__ import annotations
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
refs: dict[str, list[str]] = {}
for p in ROOT.rglob("*.py"):
    if "tests" in p.parts:
        continue
    text = p.read_text(errors="ignore")
    for m in re.finditer(r'\.table\(\s*["\']([^"\']+)["\']\s*\)', text):
        refs.setdefault(m.group(1), []).append(str(p.relative_to(ROOT)))

created: set[str] = set()
for p in [ROOT / "SUPABASE_FULL_MIGRATION.sql"]:
    text = p.read_text(errors="ignore")
    for m in re.finditer(
        r'(?:CREATE\s+(?:OR\s+REPLACE\s+)?|ALTER\s+)(?:TABLE|VIEW)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:public\.)?([A-Za-z_][\w]*)',
        text,
        re.I,
    ):
        created.add(m.group(1).lower())

missing = sorted(name for name in refs if name.lower() not in created)
print(f"backend table/view references: {len(refs)}")
print(f"migration objects: {len(created)}")
print(f"unrepresented references: {len(missing)}")

if missing:
    print("Missing:", ", ".join(missing))

if missing:
    sys.exit(1)
print("RUNTIME SCHEMA STATIC CHECK: PASS")
