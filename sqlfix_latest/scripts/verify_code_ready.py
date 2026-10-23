#!/usr/bin/env python3
"""
Code-level production readiness check (no API keys required).
Exit 0 = structure OK for managed service deploy.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []
ok: list[str] = []


def check_file(rel: str, must_contain: list[str] | None = None):
    p = ROOT / rel
    if not p.exists():
        errors.append(f"MISSING {rel}")
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    if must_contain:
        for s in must_contain:
            if s not in text:
                errors.append(f"{rel} missing: {s!r}")
                return
    try:
        if rel.endswith(".py"):
            ast.parse(text)
    except SyntaxError as e:
        errors.append(f"SYNTAX {rel}: {e}")
        return
    ok.append(rel)


REQUIRED = [
    ("app/app_service.py", ["include_router", "/health", "leads_router", "crm_router"]),
    ("app/memory/supabase_client.py", ["get_supabase", "SUPABASE_URL"]),
    ("app/middleware/auth.py", ["require_tenant", "TenantContext"]),
    ("app/middleware/service_auth.py", ["require_service_key"]),
    ("app/middleware/rate_limit.py", ["rate_limit_pipeline"]),
    ("app/api/prod/leads_api.py", ["require_control_plane", "pipeline/run"]),
    ("app/integrations/prod/pipeline.py", ["filter_blocked_leads", "post_enrich_prepare", "deliver_batch"]),
    ("app/integrations/prod/scoring.py", ["score_lead_v3"]),
    ("app/integrations/prod/suppression.py", ["filter_blocked_leads"]),
    ("app/integrations/prod/social_enrich.py", ["social_enrich_lead"]),
    ("app/integrations/prod/billing_stripe.py", ["report_usage_for_period"]),
    ("app/integrations/prod/crypto_config.py", ["encrypt_config", "load_delivery_config"]),
    ("app/integrations/prod/key_pool.py", ["KeyPoolExhausted", "mark_provider_exhausted"]),
    ("app/services/delivery.py", ["deliver_batch"]),
    ("app/crm/crm_api.py", ["require_tenant"]),
    ("Dockerfile", ["uvicorn app.app_service:app"]),
    ("docker-compose.yml", ["api:"]),
    (".env.example", ["LEAD_ENGINE_SERVICE_KEY", "SUPABASE_URL"]),
    ("SUPABASE_FULL_MIGRATION.sql", ["lead_assignments", "reserve_tenant_lead_slot", "release_tenant_lead_slot", "supabase_realtime"]),
]


for rel, must in REQUIRED:
    check_file(rel, must)

# Package inits
for d in [
    "app", "app/api", "app/api/prod", "app/crm",
    "app/integrations", "app/integrations/prod",
    "app/middleware", "app/services", "app/memory",
]:
    if not (ROOT / d / "__init__.py").exists():
        errors.append(f"MISSING {d}/__init__.py")
    else:
        ok.append(f"{d}/__init__.py")

print(f"OK files: {len(ok)}")
for e in errors:
    print(f"FAIL: {e}")
print()
if errors:
    print(f"RESULT: NOT READY — {len(errors)} issues")
    sys.exit(1)
print("RESULT: CODE PRODUCTION-READY (managed service)")
print("Next: set .env keys, apply SQL, run one live pipeline.")
sys.exit(0)
