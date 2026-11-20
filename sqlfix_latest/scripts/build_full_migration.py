"""Regenerate SUPABASE_FULL_MIGRATION.sql from supabase/migrations/*.sql (lexical order).
Run after editing any migration:  python scripts/build_full_migration.py"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
files = sorted((ROOT / "supabase" / "migrations").glob("*.sql"), key=lambda p: p.name)
out = ["-- LeadX 15.8 FULL DATABASE MIGRATION",
       "-- Single executable canonical chain. Generated from supabase/migrations/*.sql in lexical order.",
       "-- Historical docs/archive/sql/*.sql files are provenance only and are not executable chain inputs.",
       "-- This file contains every canonical migration exactly once.", "", "BEGIN;", ""]
for i, p in enumerate(files, 1):
    body = p.read_text(encoding="utf-8").lstrip("\ufeff")
    body = re.sub(r"(?im)^\s*(BEGIN|COMMIT);\s*$", "", body)  # the bundle owns the single transaction
    out += ["-- " + "=" * 76, f"-- CANONICAL MIGRATION {i:02d}: {p.name}", "-- " + "=" * 76, "", body.strip(), ""]
out.append("COMMIT;")
(ROOT / "SUPABASE_FULL_MIGRATION.sql").write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {len(files)} migrations")
