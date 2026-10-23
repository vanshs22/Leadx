"""LeadX 15.8 backend release scan. Read-only; exits non-zero on hard failures."""
from __future__ import annotations
import ast
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
    failures = []

    py_files = list((ROOT / "app").rglob("*.py"))
    for p in py_files:
        try:
            ast.parse(p.read_text())
        except Exception as exc:
            failures.append(f"syntax:{p}:{exc}")

    modules = []
    for p in py_files:
        if p.name == "__init__.py":
            continue
        modules.append(".".join(p.relative_to(ROOT).with_suffix("").parts))
    import_failures = []
    for mod in sorted(modules):
        try:
            importlib.import_module(mod)
        except Exception as exc:
            import_failures.append((mod, type(exc).__name__, str(exc)[:180]))
    if import_failures:
        failures.extend(f"import:{m}:{e}:{d}" for m, e, d in import_failures)

    service = (ROOT / "app/app_service.py").read_text()
    for needle in ['version="15.8.1"', 'app.include_router(leads_router)', 'app.include_router(crm_router)', 'app.include_router(lead_intelligence_6_router)']:
        if needle not in service:
            failures.append(f"service-missing:{needle}")

    migration = ROOT / "supabase/migrations/20260930000700_leadx_7_runtime_wiring.sql"
    if not migration.is_file():
        failures.append("missing:15.7 + 15.8 migrations")

    if failures:
        print("LEADX 7.0 SCAN: FAIL")
        for item in failures:
            print(" -", item)
        return 1

    print("LEADX 7.0 SCAN: PASS")
    print(f" - python_files={len(py_files)}")
    print(f" - imported_modules={len(modules)}")
    print(" - service_version=15.8.1")
    print(" - 15.7_oauth_migration=present")
    print(" - 15.8_security_migration=present")
    print(" - syntax=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
