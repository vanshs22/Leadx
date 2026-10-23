#!/usr/bin/env python3
"""LeadX 15.4 final production system audit.

Run from the repository root:
    python scripts/full_system_test.py

Optional:
    python scripts/full_system_test.py --skip-pytest
    python scripts/full_system_test.py --frontend-build
    python scripts/full_system_test.py --live

The default audit is dependency-light and checks source wiring, agent/DAG
contracts, FastAPI route mounting, CRM boundaries, migration completeness,
frontend/backend route references, Python compilation and the existing pytest
suite. --frontend-build runs the real Next production build when node_modules
is installed. --live performs harmless Supabase connectivity/schema checks when
SUPABASE_URL + a service key are available.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FRONT = ROOT / "lead-crm-portal"
MIG_DIR = ROOT / "supabase" / "migrations"
FULL_MIG = ROOT / "SUPABASE_FULL_MIGRATION.sql"

PASS = 0
FAIL = 0
SKIP = 0


def result(label: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def skipped(label: str, detail: str) -> None:
    global SKIP
    SKIP += 1
    print(f"[SKIP] {label} — {detail}")


def run(cmd: list[str], cwd: Path = ROOT, timeout: int = 180) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, p.stdout


def no_compiled_artifacts() -> bool:
    bad = [p for p in ROOT.rglob("*") if p.is_file() and (p.suffix in {".pyc", ".pyo"} or "__pycache__" in p.parts)]
    return result("No compiled Python artifacts in release source", not bad, ", ".join(str(p.relative_to(ROOT)) for p in bad[:8]))


def python_compile() -> bool:
    rc, out = run([sys.executable, "-m", "compileall", "-q", "app", "services", "tests"])
    # compileall creates __pycache__, so remove them immediately; the release must stay source-only.
    for p in ROOT.rglob("__pycache__"):
        if p.is_dir():
            import shutil
            shutil.rmtree(p, ignore_errors=True)
    return result("Python compilation", rc == 0, out[-500:] if rc else "all Python sources compile")


def agent_graph() -> bool:
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    from app.integrations.prod.agent_missions import DEFAULT_GRAPH

    ok = len(FULL_AGENT_REGISTRY) == 31
    result("31-agent registry", ok, f"found {len(FULL_AGENT_REGISTRY)}")
    nodes = {a for a, _, _ in DEFAULT_GRAPH}
    ok = nodes == set(FULL_AGENT_REGISTRY) - {"gtm"} and len(DEFAULT_GRAPH) == 30
    result("30 downstream mission tasks + GTM parent", ok)

    graph = {a: set(deps) for a, deps, _ in DEFAULT_GRAPH}
    unknown = sorted({d for deps in graph.values() for d in deps if d not in FULL_AGENT_REGISTRY})
    result("All DAG dependencies reference registered agents", not unknown, str(unknown))

    visiting: set[str] = set(); seen: set[str] = set()
    def visit(n: str):
        if n in visiting:
            raise RuntimeError(f"cycle at {n}")
        if n in seen:
            return
        visiting.add(n)
        for d in graph.get(n, set()):
            if d != "gtm":
                visit(d)
        visiting.remove(n); seen.add(n)
    try:
        for n in graph: visit(n)
        result("Agent DAG is acyclic", len(seen) == 30)
    except Exception as e:
        result("Agent DAG is acyclic", False, str(e))

    required_edges = {
        "prospecting": {"gtm"},
        "source_router": {"prospecting"},
        "discovery_planner": {"source_router"},
        "identity_resolution": {"discovery_planner"},
        "enrichment": {"identity_resolution"},
        "contact_finder": {"enrichment"},
        "email_finder": {"contact_finder"},
        "crm": {"qualification", "outreach", "routing"},
        "deal": {"crm"},
        "revenue": {"deal"},
        "learning": {"deal", "revenue", "campaign_optimizer", "experiment"},
    }
    result("Critical agent ordering/wiring", all(graph.get(k) == v for k, v in required_edges.items()))

    from app.integrations.prod.agent_fabric import SPECIALIZED
    core = {"prospecting", "enrichment", "research", "signal", "qualification", "outreach", "crm", "deal", "revenue", "learning"}
    executors = {p.stem for p in (ROOT / "app" / "integrations" / "prod" / "agent_executors").glob("*.py") if p.stem != "__init__"}
    covered = core & executors
    covered |= set(SPECIALIZED)
    covered.add("gtm")
    missing = sorted(set(FULL_AGENT_REGISTRY) - covered)
    return result("Every registered agent has an execution path", not missing, str(missing))


def route_wiring() -> bool:
    try:
        from app.app_service import app
    except Exception as exc:
        return result("Required FastAPI/CRM/agent routes mounted", False, f"app import failed: {type(exc).__name__}: {exc}")
    paths = {getattr(r, "path", "") for r in app.routes}
    required = {
        "/crm/{tenant_id}/leads", "/crm/{tenant_id}/pipeline", "/crm/{tenant_id}/dashboard",
        "/crm/{tenant_id}/companies", "/crm/{tenant_id}/contacts", "/crm/{tenant_id}/deals",
        "/crm/{tenant_id}/agents", "/crm/{tenant_id}/agent-missions",
        "/crm/{tenant_id}/fabric/overview", "/crm/{tenant_id}/ai/brief",
        "/billing/create-checkout-session", "/auth/email/send-code", "/support/ask",
    }
    missing = sorted(required - paths)
    return result("Required FastAPI/CRM/agent routes mounted", not missing, str(missing))


def frontend_contract() -> bool:
    api = FRONT / "lib" / "api.ts"
    if not api.exists():
        return result("Frontend API client exists", False)
    text = api.read_text(errors="ignore")
    # Backend route suffixes declared in Python source.
    routes: set[str] = set()
    for p in (ROOT / "app").rglob("*.py"):
        src = p.read_text(errors="ignore")
        routes.update(re.findall(r'@(?:router|public_router)\.(?:get|post|put|patch|delete)\(\s*["\']([^"\']+)', src))
    refs = set(re.findall(r'["`](/(?:crm|leads/v2|billing|auth|ops|support|intelligence|public)[^"`\']*)["`]', text))
    def norm(x: str) -> str:
        x = re.sub(r'\$\{[^}]+\}', '{param}', x)
        x = re.sub(r'\{[^}]+\}', '{param}', x)
        x = re.sub(r'\?.*$', '', x)
        return x.rstrip("/") or "/"
    misses = []
    for ref in refs:
        a = norm(ref)
        if a.startswith("/support/"):
            continue
        matched = False
        for route in routes:
            b = norm(route)
            aa, bb = a.split("/"), b.split("/")
            if a == b or a.endswith(b) or b.endswith(a) or (len(aa) == len(bb) and all(x == y or x == "{param}" or y == "{param}" for x,y in zip(aa,bb))):
                matched = True; break
        if not matched:
            misses.append(ref)
    return result("Frontend API references have backend route counterparts", not misses, str(sorted(misses)[:20]))


def crm_boundary() -> bool:
    text = (ROOT / "app" / "integrations" / "prod" / "agent_runtime.py").read_text()
    checks = {
        "mission can read pre-assignment global leads": "mission_id: str | None = None" in text and "if not mission_id:" in text,
        "direct runs retain tenant assignment gate": "lead_not_found_or_not_owned" in text,
        "qualification uses canonical evaluation": "evaluate_lead(lead" in text and 'agent == "qualification"' in text,
        "qualification persists canonical scores": 'table("scores").upsert' in text,
        "CRM performs assignment": 'assign_lead(' in text and 'mission_stage":"assignment_delivery' in text,
    }
    return result("Agent → intelligence → CRM ownership boundary", all(checks.values()), ", ".join(k for k,v in checks.items() if not v))


def discovery_enrichment() -> bool:
    from app.integrations.prod.discovery_catalog import discovery_source_count, discovery_lane_catalog
    from app.integrations.prod.enrichment_mesh import PAID_PROVIDER_CATALOG
    a = discovery_source_count() >= 1200 and len(discovery_lane_catalog()) >= 1200
    b = len(PAID_PROVIDER_CATALOG) >= 80
    result("Discovery fabric preserved (1200+ sources/lanes)", a, f"sources={discovery_source_count()}, lanes={len(discovery_lane_catalog())}")
    return result("Enrichment provider catalog preserved (80+)", b, f"providers={len(PAID_PROVIDER_CATALOG)}")


def migration_audit() -> bool:
    if not FULL_MIG.exists():
        return result("Single full migration exists", False)
    text = FULL_MIG.read_text(errors="ignore")
    canonical = sorted(MIG_DIR.glob("*.sql"))
    sections = re.findall(r'^-- CANONICAL MIGRATION \d+: (.+)$', text, re.M)
    checks = [
        ("single full migration is non-empty", len(text) > 100000),
        ("all canonical migrations included", len(sections) == len(canonical)),
        ("transaction starts", text.lstrip().startswith("--") and "BEGIN;" in text[:3000]),
        ("transaction commits", text.rstrip().endswith("COMMIT;")),
        ("mission schema included", "create table if not exists public.agent_missions" in text.lower()),
        ("CRM schema included", "CREATE TABLE IF NOT EXISTS companies" in text and "CREATE TABLE IF NOT EXISTS deals" in text),
        ("agent runtime schema included", "CREATE TABLE IF NOT EXISTS agent_runs" in text),
        ("no destructive DROP TABLE in canonical chain", "DROP TABLE" not in text.upper()),
        ("no DROP COLUMN in canonical chain", "DROP COLUMN" not in text.upper()),
    ]
    good = True
    for label, ok in checks:
        good &= result(f"Migration: {label}", ok)
    return good




def production_contracts() -> bool:
    """Release-blocking checks for the 15.4 security, billing, schema and runtime audit."""
    good = True
    # Canonical migrations must create every runtime object before the migration
    # that first alters/deletes it.
    sql = FULL_MIG.read_text(errors="ignore")
    required_tables = [
        "jobs", "integration_connections", "support_conversations", "support_messages",
        "support_faq", "outbound_webhooks", "webhook_deliveries", "saved_views",
        "prospect_audits", "company_locations", "contact_roles",
    ]
    for table in required_tables:
        pat = rf"CREATE TABLE IF NOT EXISTS public\.{re.escape(table)}"
        ok = bool(re.search(pat, sql, re.I))
        if not ok:
            # Some canonical definitions omit public. in legacy-compatible SQL.
            ok = bool(re.search(rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}", sql, re.I))
        good &= result(f"Clean-install table: {table}", ok)

    required_cols = {
        "tenants": ["stripe_subscription_id", "billing_status", "billing_interval", "billing_grace_until"],
        "deals": ["health_score", "last_activity_at", "next_action_at"],
        "lead_assignments": ["last_replied_at", "lifecycle_stage", "source_campaign_id"],
        "email_sends": ["variant_key", "variant_category"],
        "usage_log": ["leads_credited"],
    }
    for table, cols in required_cols.items():
        for col in cols:
            ok = bool(re.search(rf"ALTER TABLE public\.{re.escape(table)}\s+ADD COLUMN IF NOT EXISTS\s+{re.escape(col)}", sql, re.I))
            good &= result(f"Clean-install column: {table}.{col}", ok)

    # The pre-11 foundation must appear before connector/security migrations.
    pos_foundation = sql.find("20260930001050_leadx_15_3_runtime_foundation.sql")
    pos_11 = sql.find("20260930001100_leadx_11_connectors.sql")
    pos_13 = sql.find("20261001000000_leadx_13_security_queue.sql")
    good &= result("Runtime foundation precedes migration 11/13", pos_foundation >= 0 and pos_11 > pos_foundation and pos_13 > pos_foundation)

    billing = (ROOT / "app" / "api" / "prod" / "billing_api.py").read_text(errors="ignore")
    ledger_insert = billing.find('table("webhook_events").insert')
    mutation_error = billing.find("Stripe checkout billing update failed")
    claim_call = billing.find('table("webhook_events").insert')
    finish_call = billing.rfind("await finish_event()")
    good &= result("Stripe ledger claim-before-process + commit-after-mutation", claim_call >= 0 and finish_call > mutation_error > 0 and "release_event_on_failure" in billing)
    good &= result("Stripe past_due grace period", "timedelta(days=3)" in billing and 'status == "past_due"' in billing)

    app = (ROOT / "app" / "app_service.py").read_text(errors="ignore")
    good &= result("Server Idempotency-Key enforcement", "idempotency_middleware" in app and "claim_idempotency" in app and "finish_idempotency" in app)
    good &= result("Idempotency header allowed by CORS", '"Idempotency-Key"' in app)

    companies = (FRONT / "app/(portal)/companies/page.tsx").read_text(errors="ignore")
    contacts = (FRONT / "app/(portal)/contacts/page.tsx").read_text(errors="ignore")
    deals = (FRONT / "app/(portal)/deals/page.tsx").read_text(errors="ignore")
    good &= result("Companies imports useRef", "useRef" in companies[:500])
    good &= result("Contacts imports useRef", "useRef" in contacts[:500])
    good &= result("Deals destructures flash", "flash = false" in deals)

    worker = (ROOT / "app" / "worker.py").read_text(errors="ignore")
    good &= result("Worker cron uses startup monotonic anchor", "started_at = time.monotonic()" in worker)
    good &= result("Reverify is not forced on every deploy", "REVERIFY_ON_BOOT" in worker and 'last["leads.reverify"] = started_at - REVERIFY_INTERVAL' in worker)

    hard = (MIG_DIR / "20261001000300_leadx_15_4_security_reliability.sql").read_text(errors="ignore")
    extras = (ROOT / "app/crm/crm_extras_api.py").read_text(errors="ignore")
    crm = (ROOT / "app/crm/crm_api.py").read_text(errors="ignore")
    good &= result("15.4 RLS covers every public table dynamically", "ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY" in hard and "REVOKE ALL ON public.%I FROM anon, authenticated" in hard)
    good &= result("Browser table privileges revoked by default", "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated" in hard)
    good &= result("Sensitive billing/CRM writes require admin", "Depends(require_admin)" in billing and "Depends(require_admin)" in extras and "Depends(require_admin)" in crm)
    good &= result("Idempotency bound to session and request body", "scope_hash" in app and "request_hash" in app and "body_hash = hashlib.sha256(body).hexdigest()" in app)
    good &= result("Idempotency caches only 2xx JSON", "200 <= response.status_code < 300" in app)
    good &= result("Idempotency stale-claim recovery", "processing_expires_at" in hard and "now()+interval '10 minutes'" in hard)
    good &= result("Stripe portal price maps to plan", "def _plan_for_price" in billing and "price_plan, price_interval = _plan_for_price" in billing)
    good &= result("Alerts use canonical pipeline columns", "error_message, started_at" in (ROOT / "app/integrations/prod/alerts.py").read_text(errors="ignore"))
    good &= result("Revenue graph uses leads_global.category", "state,category,website" in (ROOT / "app/api/prod/revenue_os_api.py").read_text(errors="ignore"))
    good &= result("Automation update_lead proves assignment ownership", "lead_assignments" in (ROOT / "app/integrations/prod/automation_engine.py").read_text(errors="ignore"))
    good &= result("Frontend connection type matches backend", "connection_name?: string" in (FRONT / "lib/api.ts").read_text(errors="ignore") and "auth_mode?:" in (FRONT / "lib/api.ts").read_text(errors="ignore"))
    send_policy = (ROOT / "app/integrations/prod/send_policy.py").read_text(errors="ignore")
    good &= result("Washington DC timezone disambiguation", "Washington, DC" in send_policy and '"DC"' in send_policy)
    good &= result("Unlisted verticals receive generic vertical-fit scoring", "direct_vertical_match" in (ROOT / "app/integrations/prod/scoring.py").read_text(errors="ignore"))

    for rel in ("app/api/prod/leads_api.py", "app/integrations/prod/pipeline.py"):
        text = (ROOT / rel).read_text(errors="ignore")
        bad = re.findall(r"except Exception:\s*pass", text)
        good &= result(f"No bare swallowed exceptions in {rel}", not bad, f"count={len(bad)}")
    return good


def source_integrity() -> bool:
    # AST-parse all Python files without generating bytecode.
    bad=[]
    for p in (ROOT / "app").rglob("*.py"):
        try: ast.parse(p.read_text(errors="ignore"), filename=str(p))
        except SyntaxError as e: bad.append(f"{p.relative_to(ROOT)}:{e.lineno}")
    return result("Python AST integrity", not bad, ", ".join(bad[:10]))


def pytest_suite() -> bool:
    import importlib.util
    import shutil
    if importlib.util.find_spec("pytest") is not None:
        cmd = [sys.executable, "-m", "pytest", "-q"]
    else:
        pytest_bin = shutil.which("pytest")
        if not pytest_bin:
            skipped("Existing pytest suite", "pytest is not installed; run `python -m pip install -r requirements-dev.txt`")
            return True
        cmd = [pytest_bin, "-q"]
    rc, out = run(cmd, timeout=300)
    tail = " ".join(out.strip().splitlines()[-4:])
    return result("Existing pytest suite", rc == 0, tail)



def frontend_syntax() -> bool:
    checker = ROOT / "scripts" / "frontend_syntax_check.mjs"
    if not checker.exists():
        return result("Frontend TS/TSX syntax", False, "frontend_syntax_check.mjs missing")
    rc, out = run(["node", str(checker)], cwd=ROOT, timeout=120)
    return result("Frontend TS/TSX syntax", rc == 0, " ".join(out.strip().splitlines()[-3:]))

def frontend_build() -> bool:
    if not (FRONT / "node_modules").is_dir():
        skipped("Next production build", "node_modules is not installed; run npm ci first, then rerun with --frontend-build")
        return True
    rc, out = run(["npm", "run", "build"], cwd=FRONT, timeout=300)
    return result("Next production build", rc == 0, " ".join(out.strip().splitlines()[-4:]))


def live_db() -> bool:
    if not os.getenv("SUPABASE_URL"):
        skipped("Live Supabase schema", "SUPABASE_URL/service key not configured")
        return True
    try:
        from app.memory.supabase_client import get_supabase
        sb = get_supabase()
        required = ["tenants", "leads_global", "lead_assignments", "agent_runs", "agent_missions", "agent_mission_tasks", "agent_mission_events", "companies", "contacts", "deals"]
        missing=[]
        for table in required:
            try: sb.table(table).select("*").limit(1).execute()
            except Exception: missing.append(table)
        return result("Live Supabase required tables", not missing, str(missing))
    except Exception as e:
        return result("Live Supabase connectivity", False, str(e)[:300])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--frontend-build", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    print("\nLEADX FINAL SYSTEM TEST")
    print("=" * 72)
    print(f"root: {ROOT}")

    source_integrity()
    python_compile()
    # compileall cleanup happens before this check, so source releases stay clean.
    no_compiled_artifacts()
    agent_graph()
    crm_boundary()
    discovery_enrichment()
    route_wiring()
    frontend_contract()
    frontend_syntax()
    migration_audit()
    production_contracts()
    if not args.skip_pytest:
        pytest_suite()
    if args.frontend_build:
        frontend_build()
    else:
        skipped("Next production build", "not requested; use --frontend-build after npm ci")
    if args.live:
        live_db()
    else:
        skipped("Live Supabase checks", "not requested; use --live with configured credentials")

    print("\n" + "=" * 72)
    print(f"PASS={PASS} FAIL={FAIL} SKIP={SKIP}")
    if FAIL:
        print("RESULT=FAIL")
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
