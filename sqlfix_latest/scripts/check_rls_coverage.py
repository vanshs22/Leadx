"""Fail CI when tenant-scoped public tables do not have RLS enabled."""
from pathlib import Path
import re, sys
root=Path(__file__).resolve().parents[1]
# Static migration audit: tables with tenant_id must have an ENABLE ROW LEVEL SECURITY
# statement somewhere in the canonical timestamped chain.
migs=sorted((root/'supabase'/'migrations').glob('*.sql'))
text='\n'.join(p.read_text(errors='ignore') for p in migs)
tables=set(re.findall(r'create\s+table(?:\s+if\s+not\s+exists)?\s+(?:public\.)?([a-zA-Z_][\w]*)', text, re.I))
# 15.4 uses a global public-table hardening block because the browser never
# talks directly to PostgREST. This is stronger than an allow-list: every current
# table is covered, including non-tenant tables such as platform_admins/jobs.
global_hardening = (
    "ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY" in text
    and "REVOKE ALL ON public.%I FROM anon, authenticated" in text
)
missing=[]
for t in tables:
    defs=re.findall(r'create\s+table(?:\s+if\s+not\s+exists)?\s+(?:public\.)?'+re.escape(t)+r'\s*\((.*?)\);', text, re.I|re.S)
    has_explicit = bool(re.search(r'alter\s+table(?:\s+if\s+exists)?\s+(?:public\.)?'+re.escape(t)+r'\s+enable\s+row\s+level\s+security', text, re.I))
    if not (global_hardening or has_explicit):
        missing.append(t)
if missing:
    print('RLS missing for public tables:', ', '.join(sorted(missing))); sys.exit(1)
if not global_hardening:
    print('RLS global hardening block missing'); sys.exit(1)
print(f'RLS coverage OK: {len(tables)} public tables audited; global hardening enabled')
