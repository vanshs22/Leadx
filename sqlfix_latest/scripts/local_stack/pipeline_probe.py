import json, time
from e2e_lib import *
admin = seed_admin(); A = seed_tenant("Probe Dental"); ca = client(admin["token"]); a = client(A["token"], A["tenant_id"])
ok_ = lambda r: (r.raise_for_status(), r.json())[1]
a.post(f"/crm/{A['tenant_id']}/setup")
r = ca.post("/leads/v2/pipeline/run", json={"tenant_id": A["tenant_id"], "industry": "dental", "locations": ["Austin, TX"], "target_new_leads": 10, "limit_per_location": 10, "deliver": False, "use_crawl4ai": False, "use_firecrawl": False, "include_maps": True, "include_web": False})
print(r.status_code, r.text[:400]); d = r.json(); rid = d.get("run_id")
t0 = time.time(); last = None
while time.time() - t0 < 240 and rid:
    rows = db("GET", f"pipeline_runs?id=eq.{rid}&select=*")
    p = rows[0] if rows else {}
    s = json.dumps({k: p.get(k) for k in ("status", "discovered", "enriched", "gated", "assigned_count", "delivered_count", "stage", "progress_message", "stop_reason", "last_heartbeat_at")}, default=str)[:420]
    if s != last: print(round(time.time() - t0), s); last = s
    if str(p.get("status")) in ("completed", "failed", "cancelled", "succeeded", "done", "stopped"): break
    time.sleep(5)
print("RUN", rid, json.dumps(p, default=str)[:1500])
print("assignments", len(db("GET", f"lead_assignments?tenant_id=eq.{A['tenant_id']}&select=id")))
print("jobs", json.dumps(db("GET", "jobs?select=kind,status,attempts&limit=20"), default=str)[:900])
print("gates", json.dumps(db("GET", "lead_quality_gates?select=decision,reasons&limit=30"), default=str)[:900])
