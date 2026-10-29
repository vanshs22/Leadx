#!/usr/bin/env python3
"""Static accessibility regression gate for the portal."""
from pathlib import Path
import re, sys
root=Path(__file__).resolve().parents[1]/'lead-crm-portal'
paths=list((root/'app').rglob('*.tsx'))+list((root/'components').rglob('*.tsx'))
text='\n'.join(p.read_text(encoding='utf-8', errors='ignore') for p in paths)
aria=len(re.findall(r'\baria-[a-z-]+\s*=', text))
buttons=re.findall(r'<button\b([^>]*)>(.*?)</button>', text, re.S)
# A visible button name is an accessible name even without a redundant aria-label.
def _visible_name(body):
    body=re.sub(r'<[^>]+>', ' ', body)
    body=re.sub(r'\{[^{}]*\}', ' ', body)
    return bool(re.sub(r'\s+', '', body))
labels=sum(bool(re.search(r'\baria-label\s*=', attrs)) or _visible_name(body) for attrs, body in buttons)
skip=('href="#main-content"' in (root/'app/(portal)/layout.tsx').read_text(encoding='utf-8') and 'href="#main-content"' in (root/'app/(admin)/layout.tsx').read_text(encoding='utf-8'))
print(f'aria_attributes={aria}')
print(f'buttons={len(buttons)}')
print(f'buttons_with_accessible_name={labels}')
print(f'skip_links={int(skip)}')
ok=aria>=200 and labels>=200 and skip
print('ACCESSIBILITY GATE:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
