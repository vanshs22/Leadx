from pathlib import Path
import ast, json, sys
root=Path(__file__).resolve().parents[1]
front=root/'lead-crm-portal'
checks=[]
def check(name, ok, detail=''): checks.append((name,bool(ok),detail))
check('frontend present', front.is_dir(), str(front))
check('frontend package', (front/'package.json').is_file())
check('pipeline UI', (front/'app/(portal)/pipeline/page.tsx').is_file())
check('lead detail UI', (front/'app/(portal)/leads/[id]/page.tsx').is_file())
check('integrations UI', (front/'app/(portal)/settings/integrations/page.tsx').is_file())
for p in ['discovery_layer.py','enrichment_layer.py','research_layer.py','scoring_layer.py','persistence_layer.py','providers.py','reliability.py','repositories.py']:
    f=root/'app/layers'/p
    try: ast.parse(f.read_text()); ok=True; d='syntax ok'
    except Exception as e: ok=False; d=str(e)
    check(f'layer {p}',ok,d)
check('canonical layer migration', any(root.glob('supabase/migrations/*_leadx_14_data_intelligence_fabric.sql')))
for n,ok,d in checks: print(('PASS' if ok else 'FAIL'), n, d)
failed=[x for x in checks if not x[1]]
print(f'RESULT: {len(checks)-len(failed)}/{len(checks)} checks passed')
sys.exit(1 if failed else 0)
