"""Call every parameterless GET route as admin and as tenant owner; report 5xx and slow responses."""
import re, time, json, httpx
from e2e_lib import *
spec = httpx.get(API + "/openapi.json").json()
admin, ta = seed_admin(), seed_tenant("Sweep Co")
ca, ct = client(admin["token"]), client(ta["token"], ta["tenant_id"])
rows = []
for path, ops in sorted(spec["paths"].items()):
    ops = ops
    if "get" not in ops: continue
    path = path.replace("{tenant_id}", ta["tenant_id"]); path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
    kind = "admin" if ("/admin" in path or path.startswith("/platform")) else "tenant"
    c = ca if kind == "admin" else ct
    t0 = time.time()
    try: r = c.get(path); code = r.status_code; body = r.text[:160].replace("\n", " ")
    except Exception as e: code = -1; body = repr(e)[:160]
    rows.append((code, kind, path, round((time.time() - t0) * 1000), body))
from collections import Counter
print("status histogram:", dict(Counter(r[0] for r in rows)), "total", len(rows))
for r in rows:
    if r[0] >= 500 or r[0] == -1: print("5XX", r)
print("slow(>1500ms):", [(r[2], r[3]) for r in rows if r[3] > 1500])
print("401/403 on tenant routes:", [(r[0], r[2]) for r in rows if r[0] in (401, 403) and r[1] == "tenant"][:15])
print("401/403 on admin routes:", [(r[0], r[2]) for r in rows if r[0] in (401, 403) and r[1] == "admin"][:15])
print("4xx:", [(r[0], r[2].replace(ta["tenant_id"], "{T}"), r[4][:70]) for r in rows if r[0] in (400, 404)])
