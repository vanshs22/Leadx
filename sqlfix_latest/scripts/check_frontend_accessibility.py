#!/usr/bin/env python3
"""Static accessibility regression gate for the portal."""
from pathlib import Path
import re, sys
root=Path(__file__).resolve().parents[1]/'lead-crm-portal'
text='\n'.join(p.read_text(errors='ignore') for p in list((root/'app').rglob('*.tsx'))+list((root/'components').rglob('*.tsx')))
aria=len(re.findall(r'\baria-[a-z-]+\s*=', text))
buttons=len(re.findall(r'<button\b', text))
labels=len(re.findall(r'<button\b[^>]*\baria-label\s*=', text))
skip=('href="#main-content"' in (root/'app/(portal)/layout.tsx').read_text() and 'href="#main-content"' in (root/'app/(admin)/layout.tsx').read_text())
print(f'aria_attributes={aria}')
print(f'buttons={buttons}')
print(f'buttons_with_aria_label={labels}')
print(f'skip_links={int(skip)}')
ok=aria>=200 and labels>=200 and skip
print('ACCESSIBILITY GATE:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
