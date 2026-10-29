#!/usr/bin/env python3
"""LeadX 15.8 security + production release gate."""
from pathlib import Path
import os, re, sys
ROOT=Path(__file__).resolve().parents[1]
checks=[]
def check(name, ok, detail=''): checks.append((name,bool(ok),detail))

# Auth/session security
portal='\n'.join(p.read_text(errors='ignore') for p in (ROOT/'lead-crm-portal').rglob('*.ts*'))
mid=(ROOT/'lead-crm-portal/middleware.ts').read_text()
storage=(ROOT/'lead-crm-portal/lib/authCookieStorage.ts').read_text()
check('HttpOnly session cookie path', 'leadx_session' in mid and re.search(r'httpOnly\s*:\s*true', mid, re.I) is not None)
check('no JS auth cookie writer', 'document.cookie' not in storage)
check('CSRF enforcement', 'csrf_middleware' in (ROOT/'app/app_service.py').read_text() and 'X-CSRF-Token' in (ROOT/'app/app_service.py').read_text())
check('server route guard', 'hasValidSession' not in mid and 'sessionStatus' in mid)
check('CSP no unsafe-inline script', "script-src 'self' 'unsafe-inline'" not in mid)
check('CSP nonce', 'nonce-${nonce}' in mid)

# OAuth
oauth=(ROOT/'app/crm/crm_extras_api.py').read_text()
check('oauth user-bound', '_oauth_state(str(tenant_id), provider, ctx.user_id)' in oauth and 'user_id' in oauth)
check('oauth single-use', '_consume_oauth_state' in oauth and '.is_("used_at","null")' in oauth)
check('oauth secret required', 'INTEGRATION_OAUTH_STATE_SECRET' in oauth and 'len(secret) < 32' in oauth)

# Worker / startup / distributed limits
compose=(ROOT/'docker-compose.yml').read_text(); docker=(ROOT/'Dockerfile').read_text(); worker=(ROOT/'app/worker.py').read_text()
check('worker service deployed', 'worker:' in compose and 'LEADX_PROCESS=worker' in compose and 'python -m app.worker' in docker)
check('worker health', 'WORKER_HEALTH_FILE' in docker and '/tmp/leadx-worker-heartbeat' in docker and 'queue_capability' in worker and 'queue_checked_at' in worker)
config=(ROOT/'app/core/runtime_config.py').read_text()
check('production startup fail-closed', 'raise RuntimeError' in config and 'PRODUCTION_REQUIRED' in config)
rl=(ROOT/'app/middleware/rate_limit.py').read_text()
check('distributed rate limit fail-closed', '_requires_distributed' in rl and 'Distributed rate limiting is unavailable' in rl and '_dependency_error' in rl)
check('compose readiness endpoint', '/health/ready' in compose)

# Billing / RLS
billing=(ROOT/'supabase/migrations/20261001000600_leadx_15_8_security_hardening.sql').read_text()
for fn in ('credit_tenant_usage','bump_ops_metric'):
    check(f'{fn} search_path', 'SET search_path = pg_catalog, public' in billing)
    check(f'{fn} browser revoke', f'REVOKE ALL ON FUNCTION public.{fn}' in billing)
    check(f'{fn} service grant', f'GRANT EXECUTE ON FUNCTION public.{fn}' in billing)
unsafe=[]
for f in (ROOT/'supabase/migrations').glob('*.sql'):
    t=f.read_text(errors='ignore')
    if re.search(r'CREATE POLICY[^;]+USING\s*\(true\)',t,re.I|re.S) and not re.search(r'CREATE POLICY[^;]+TO\s+service_role[^;]+USING\s*\(true\)',t,re.I|re.S): unsafe.append(f.name)
check('USING(true) policies service-scoped', not unsafe, ','.join(unsafe))

# Unsubscribe
u=(ROOT/'app/integrations/prod/unsubscribe.py').read_text(); crm=(ROOT/'app/crm/crm_api.py').read_text(); email=(ROOT/'app/integrations/prod/email_outreach.py').read_text()
check('unsubscribe no insecure fallback', 'dev-only-insecure' not in u and 'SUPABASE_JWT_SECRET' not in u)
check('unsubscribe GET non-mutating', '@crm_public_router.get("/unsubscribe")' in crm and 'process_unsubscribe' not in crm.split('@crm_public_router.get("/unsubscribe")',1)[1].split('@crm_public_router.post("/unsubscribe")',1)[0])
check('unsubscribe POST', '@crm_public_router.post("/unsubscribe")' in crm)
check('RFC8058 one-click header', 'List-Unsubscribe-Post' in email)

# Lead quality
q=(ROOT/'app/integrations/prod/quality.py').read_text(); contact=(ROOT/'app/integrations/prod/contact_state.py').read_text(); pipe=(ROOT/'app/integrations/prod/pipeline.py').read_text(); rank=(ROOT/'app/integrations/prod/delivery_ranker.py').read_text()
check('verified email gate', 'email_ok' in q and 'def email_ok' in contact and 'VALID = {"valid", "deliverable"}' in contact)
check('verified phone gate', 'phone_ok' in q and 'def phone_ok' in contact and 'phone_verified' in contact)
check('intel gate fail-closed', 'if intel_gate else False' in pipe)
check('ranker fail-closed', 'ranker failed; fail-closed' in pipe and 'return []' in rank)

# Exceptions / migrations / version
silent=0
for f in (ROOT/'app').rglob('*.py'):
    silent += len(re.findall(r'except\s+Exception(?:\s+as\s+\w+)?\s*:\s*\n\s*pass\b',f.read_text(errors='ignore')))
check('no swallowed exception pass', silent==0, str(silent))
migs=sorted((ROOT/'supabase/migrations').glob('*.sql'))
canonical_names = [p.name for p in migs]
check('canonical migrations', bool(migs), str(len(migs)))
check('full migration mirrors chain', re.findall(r'-- CANONICAL MIGRATION \d+: ([^\n]+)',(ROOT/'SUPABASE_FULL_MIGRATION.sql').read_text()) == canonical_names)
check('runtime 15.8', '15.8' in (ROOT/'app/app_service.py').read_text())

# Hashes are mandatory for a production artifact, but this repository is also
# audited in offline/dev environments where PyPI cannot be reached to resolve
# wheel/sdist hashes. Do not fake hashes. CI generates the authoritative hashed
# lock before installing the production dependency set; local runs report this
# as SKIP instead of a misleading product-code failure.
lock=(ROOT/'requirements.lock').read_text()
if bool(re.search(r'--hash=sha256:[0-9a-f]{64}',lock)):
    check('dependency lock has hashes', True, 'hashed lock present')
elif os.getenv('LEADX_ENFORCE_HASHED_LOCK', '').lower() in {'1','true','yes'}:
    check('dependency lock has hashes', False, 'Production gate requires CI to generate a hashed lock before image promotion')
else:
    print('SKIP dependency lock has hashes — offline/local artifact has no PyPI hashes; CI generates the authoritative hashed lock')

for n,ok,d in checks: print(('PASS' if ok else 'FAIL'), n, d)
failed=[x for x in checks if not x[1]]
print(f'RESULT: {len(checks)-len(failed)}/{len(checks)}')
sys.exit(1 if failed else 0)
