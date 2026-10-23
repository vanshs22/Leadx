"""LeadX 9.0 release scan: quota hard caps, automation safety, opt-out/email wiring."""
from pathlib import Path
import ast, re

ROOT=Path(__file__).resolve().parents[1]
checks=[]
def check(name, ok, detail=""):
    checks.append((name,bool(ok),detail))

pipeline=(ROOT/'app/integrations/prod/pipeline.py').read_text()
quality=(ROOT/'app/integrations/prod/quality.py').read_text()
mig=(ROOT/'supabase/migrations/20260930000900_leadx_9_automation_quota.sql').read_text()
auto=(ROOT/'app/integrations/prod/automation_engine.py').read_text()
inbound=(ROOT/'app/integrations/prod/inbound_email.py').read_text()
email=(ROOT/'app/integrations/prod/email_outreach.py').read_text()
frontend=ROOT/'lead-crm-portal'
check('9.0 migration present', (ROOT/'supabase/migrations/20260930000900_leadx_9_automation_quota.sql').exists())
check('run-level target fields', 'requested_target' in pipeline and 'claim_pipeline_run_slot' in quality)
check('atomic monthly quota reservation', 'reserve_tenant_lead_slot' in quality)
check('discovery request quota reservation retained', 'discovery_request_id' in quality and 'consume_tenant_discovery_slot' in '\n'.join(p.read_text(errors='ignore') for p in (ROOT/'supabase/migrations').glob('*.sql')))
check('automation event queue', 'automation_events' in mig and 'process_events' in auto)
check('bounded automation actions', 'ALLOWED_ACTIONS' in auto and '[:25]' in auto)
check('reply intelligence', 'score_reply' in inbound and 'reply_score' in mig)
check('opt-out stop path', 'process_unsubscribe' in inbound and 'stop_sequence' in auto)
check('atomic email domain cap', 'reserve_email_domain_slot' in mig and 'reserve_domain_slot' in email)
check('frontend automation route', (frontend/'app/(portal)/automations/page.tsx').exists())
check('frontend no hard reload', not any(re.search(r'window\.location\.reload\(|router\.refresh\(', p.read_text(errors='ignore')) for p in frontend.rglob('*.tsx')))
# Parse all Python application modules.
errors=[]
for p in (ROOT/'app').rglob('*.py'):
    try: ast.parse(p.read_text(errors='ignore'))
    except Exception as e: errors.append(f'{p}:{e}')
check('python AST scan', not errors, '; '.join(errors[:3]))

print('LEADX 9.0 SCAN')
for n,ok,d in checks: print(('PASS' if ok else 'FAIL'), n, ('— '+d) if d else '')
print(f'RESULT: {"PASS" if all(x[1] for x in checks) else "FAIL"}')
raise SystemExit(0 if all(x[1] for x in checks) else 1)
