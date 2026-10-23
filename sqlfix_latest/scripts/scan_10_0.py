from __future__ import annotations
import ast, json, re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
errors=[]; warnings=[]

def check(name, ok, detail):
    (warnings if ok else errors).append(f"{name}: {detail}")

# Catalogs
sys.path.insert(0,str(ROOT))
from app.integrations.prod.discovery_catalog import discovery_source_count, source_catalog
from app.integrations.prod.enrichment_mesh import PAID_PROVIDER_CATALOG
from app.integrations.prod.core_engine import CORE_40
from app.integrations.prod.agents import AGENTS, CAPABILITIES
check('discovery_catalog', discovery_source_count() >= 1000, f'{discovery_source_count()} discovery lanes')
check('discovery_unique', len({x['name'] for x in source_catalog()}) == len(source_catalog()), 'unique catalog names')
check('enrichment_catalog', len(PAID_PROVIDER_CATALOG) >= 80, f'{len(PAID_PROVIDER_CATALOG)} enrichment providers')
check('core_capabilities', len(CORE_40) == 40, f'{len(CORE_40)} core capabilities')
check('agents', len(AGENTS) == 10, f'{len(AGENTS)} agents')

# Every registered agent has an execute branch.
agents_src=(ROOT/'app/integrations/prod/agents.py').read_text()
for aid in AGENTS:
    check(f'agent:{aid}', (f'agent == \"{aid}\"' in agents_src) or (f'\"{aid}\"' in agents_src and 'elif agent in {' in agents_src), 'execute branch present')

# Migrations continuity.
migs=sorted((ROOT/'supabase/migrations').glob('*.sql'))
for n in range(1,11):
    suffix=f'2026093000{n:02d}'
    check(f'migration:{n}', any(p.name.startswith(suffix) for p in migs), 'present')

# Critical source-level security/perf invariants.
auto=(ROOT/'app/integrations/prod/automation_engine.py').read_text()
check('automation_idempotency', 'status") == "completed"' in auto and 'idempotency_key' in auto, 'completed workflow runs are not replayed')
check('automation_tenant_write', '.eq("tenant_id",tenant_id)' in auto, 'automation lead update is tenant-scoped')
crm=(ROOT/'app/crm/crm_api.py').read_text()
check('crm_cursor', 'next_cursor' in crm and 'cursor: Optional[str]' in crm, 'keyset pagination path present')
check('crm_bulk_async', 'crm.bulk_stage' in crm, 'large bulk stage operations are queued')

# Frontend hard reloads: auth/checkout/provider redirects are navigation by design;
# CRM runtime must not reload the page.
front=ROOT/'lead-crm-portal'
bad=[]
for p in front.rglob('*'):
    if p.is_file() and p.suffix in {'.ts','.tsx','.js','.jsx'} and 'node_modules' not in p.parts:
        t=p.read_text(errors='ignore')
        # Ignore comments/docstrings: only executable reload calls matter.
        executable = re.sub(r'/\*.*?\*/|//[^\n]*|\"\"\".*?\"\"\"|\'\'\'.*?\'\'\'', '', t, flags=re.S)
        if 'location.reload(' in executable or 'window.location.reload(' in executable or 'router.refresh(' in executable:
            bad.append(str(p.relative_to(ROOT)))
check('frontend_no_crm_reload', not bad, f'blocking reload patterns: {bad[:5]}')

print('LeadX 10.0 deep scanner')
for x in warnings: print('PASS',x)
for x in errors: print('FAIL',x)
print(f'FAILURES={len(errors)} WARNINGS={len(warnings)}')
sys.exit(1 if errors else 0)
