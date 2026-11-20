import json, time
from e2e_lib import *
A = seed_tenant("GTM Co"); a = client(A["token"], A["tenant_id"]); T = A["tenant_id"]
r = a.post(f"/crm/{T}/agent-missions", json={"objective": "Find and qualify dental clinics in Austin, TX", "input_data": {"vertical": "dental", "locations": ["Austin, TX"], "target_new_leads": 3}, "mode": "preview"})
print("CREATE", r.status_code, r.text[:300]); m = r.json().get("mission") or {}; mid = m.get("id")
g = {}; t0 = time.time(); last = None
while mid and time.time() - t0 < 240:
    g = a.get(f"/crm/{T}/agent-missions/{mid}").json().get("mission") or {}
    tasks = g.get("tasks") or []
    from collections import Counter
    s = json.dumps({"status": g.get("status"), "tasks": dict(Counter(x.get("status") for x in tasks)), "reason": g.get("terminal_reason")})
    if s != last: print(round(time.time() - t0), s); last = s
    if g.get("status") in ("completed", "failed", "cancelled", "blocked", "partial", "succeeded"): break
    time.sleep(6)
print("DONE", json.dumps({k: (g or {}).get(k) for k in ("status", "terminal_reason", "mode")}, default=str)); g = g if mid else {}
fails = [(x.get("agent_id"), x.get("status"), (x.get("error") or "")[:80]) for x in (g.get("tasks") or []) if x.get("status") not in ("done", "completed", "succeeded", "skipped")]
print("NOT_DONE", fails[:12])
