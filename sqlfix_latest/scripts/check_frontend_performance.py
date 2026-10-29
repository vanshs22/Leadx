from pathlib import Path
import re,sys,json
root=Path(__file__).resolve().parents[1]/'lead-crm-portal/app'
pages=list(root.rglob('page.tsx'))
client=sum(1 for p in pages if '"use client"' in p.read_text(errors='ignore')[:300])
raw=[]
for p in pages:
 s=p.read_text(errors='ignore')
 if 'window.setInterval(' in s or re.search(r'(?<![A-Za-z])setInterval\(',s): raw.append(str(p))
# Every timer, including auth-code countdowns, uses the visibility-aware helper.
assert not raw, f'raw polling/timer intervals remain: {raw}'
for p in ['(portal)/dashboard/loading.tsx','(portal)/today/loading.tsx','(portal)/leads/loading.tsx']:
 assert (root/p).is_file(), f'missing primary loading boundary: {p}'
print(json.dumps({'pages':len(pages),'client_pages':client,'primary_loading_boundaries':3,'raw_polling_pages':len(raw),'result':'PASS'},indent=2))
