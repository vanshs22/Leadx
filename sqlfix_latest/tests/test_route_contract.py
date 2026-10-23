from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]

def _frontend_paths():
    text = (ROOT.parent / "lead-crm-portal" / "lib" / "api.ts").read_text()
    # API client literals; query-string-only template fragments are ignored.
    return sorted(set(re.findall(r"[`\"](/(?:crm|leads/v2|billing|auth|ops|support|intelligence|public|api)[^`\"]*)[`\"]", text)))

def _backend_paths():
    paths=set()
    for p in (ROOT/'app').rglob('*.py'):
        t=p.read_text(errors='ignore')
        for m in re.finditer(r'@(?:router|public_router)\.(?:get|post|put|patch|delete)\(\s*[\"\']([^\"\']+)', t):
            paths.add(m.group(1))
    return paths

def _norm(p):
    p=re.sub(r'\$\{[^}]+\}', '{param}', p)
    p=re.sub(r'\?[^/]*$', '', p)
    p=re.sub(r'\{[^}]+\}', '{param}', p)
    return p.rstrip('/') or '/'

def _matches(client, route):
    a=_norm(client); b=_norm(route)
    # Router prefixes are mounted elsewhere; compare structural suffixes and params.
    if a==b: return True
    if a.endswith(b) or b.endswith(a): return True
    aa=a.split('/'); bb=b.split('/')
    if len(aa)!=len(bb): return False
    return all(x==y or x=='{param}' or y=='{param}' for x,y in zip(aa,bb))

def test_required_routes_present():
    routes=_backend_paths()
    required=['/{tenant_id}/leads','/{tenant_id}/pipeline','/{tenant_id}/dashboard']
    assert all(any(_matches(r,x) for r in routes) for x in required)

def test_frontend_api_surface_has_backend_contract():
    if not (ROOT.parent / "lead-crm-portal" / "lib" / "api.ts").is_file():
        pytest.skip("backend/core-engine release does not bundle the frontend")
    routes=_backend_paths()
    misses=[]
    for p in _frontend_paths():
        # Skip absolute OAuth/public URL fragments and dynamic query-only strings.
        if 'http' in p or '${qs}' in p: continue
        if p.startswith("/support/"): continue  # support routers use a separate client_router prefix
        if not any(_matches(p,x) for x in routes):
            misses.append(p)
    assert not misses, f"frontend API paths without FastAPI route: {misses[:25]}"
