"""LeadX 8.0 release integrity scan. Standard library only."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FR = ROOT / "lead-crm-portal"
errors=[]; notes=[]

def need(cond, msg):
    (None if cond else errors.append(msg))

need(FR.exists(), "frontend directory missing")
need((FR/'package.json').exists(), "frontend package.json missing")
need((FR/'app/(portal)/logs/page.tsx').exists(), "workspace logs page missing")
need((FR/'lib/crmRealtime.ts').exists(), "realtime coordinator missing")
need((ROOT/'supabase/migrations/20260930000800_leadx_8_frontend_runtime.sql').exists(), "8.0 migration missing")
need((ROOT/'supabase/migrations/20260930000700_leadx_7_runtime_wiring.sql').exists(), "7.0 migration missing")
need((ROOT/'app/integrations/prod/lead7_runtime.py').exists(), "7.0 runtime missing")

pkg=(FR/'package.json').read_text()
need('"version": "8.0.0"' in pkg, "frontend version is not 8.0.0")
rt=(FR/'lib/crmRealtime.ts').read_text()
need('private: true' in rt and 'broadcast' in rt and 'crm_change' in rt, "private Broadcast realtime wiring missing")
need('setAuth' in rt, "Realtime auth refresh missing")
api=(FR/'lib/api.ts').read_text()
need('GET_INFLIGHT' in api and 'GET_STALE_MS' in api, "GET dedupe/SWR cache missing")
logs=(FR/'app/(portal)/logs/page.tsx').read_text()
need('next_before' in logs and 'Load older logs' in logs, "cursor pagination missing from logs")
sidebar=(FR/'components/layout/Sidebar.tsx').read_text()
need('"/logs"' in sidebar, "Logs navigation missing")

# Check actual executable code, ignoring comments/docstrings, for hard page reloads.
for p in FR.rglob('*'):
    if p.suffix not in {'.ts','.tsx','.js','.jsx'} or 'node_modules' in p.parts or '.next' in p.parts:
        continue
    s=p.read_text(errors='ignore')
    s=re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    s=re.sub(r'(^|\s)//.*', r'\1', s)
    need('location.reload(' not in s and 'window.location.reload(' not in s and 'router.refresh(' not in s,
         f"hard refresh remains in executable frontend code: {p.relative_to(ROOT)}")

# Report useful size/coverage signals.
frontend_files=sum(1 for p in FR.rglob('*') if p.is_file() and 'node_modules' not in p.parts and '.next' not in p.parts)
notes.append(f"frontend_files={frontend_files}")
notes.append(f"portal_routes={sum(1 for p in (FR/'app').rglob('page.tsx'))}")
notes.append(f"migration_files={len(list((ROOT/'supabase/migrations').glob('*.sql')))}")
print('LEADX 8.0 SCAN:', 'PASS' if not errors else 'FAIL')
for n in notes: print(' -', n)
for e in errors: print(' - ERROR:', e)
raise SystemExit(1 if errors else 0)
