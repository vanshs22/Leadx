"""Fail when application code drifts from the canonical SaaS plan contract."""
from pathlib import Path
import ast, re, sys
ROOT=Path(__file__).resolve().parents[1]
plans=(ROOT/'app/core/plans.py').read_text()
current={m.group(1) for m in re.finditer(r'^\s*"(free|starter|growth|agency)"\s*:\s*\{', plans, re.M)}
legacy={m.group(1) for m in re.finditer(r'"(premium|enterprise)"\s*:', plans)}
if current != {'free','starter','growth','agency'} or legacy != {'premium','enterprise'}:
    raise SystemExit(f'plan source incomplete: current={current} legacy={legacy}')
violations=[]
for p in (ROOT/'app').rglob('*.py'):
    if p == ROOT/'app/core/plans.py': continue
    try: tree=ast.parse(p.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError as exc: violations.append(f'{p}: {exc}'); continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value,str) and n.value in legacy:
            violations.append(f'{p}:{getattr(n,"lineno",0)} legacy plan literal {n.value}')
if violations:
    print('\n'.join(violations)); sys.exit(1)
print(f'PLAN CONTRACT PASS: {len(current)} active plans; {len(legacy)} legacy aliases confined to plans.py')
