"""Two tenants run real discovery->enrichment->gate->assign->CRM pipelines CONCURRENTLY against the local stack.
Discovery = live OSM/Nominatim (free lane). Email verification = local test adapter (no SMTP, nothing is sent)."""
import json, os, sys, time, concurrent.futures as cf
from e2e_lib import *
R = []
def rec(name, passed, detail=""): R.append({"test": name, "status": "PASS" if passed else "FAIL", "error": "" if passed else str(detail)[:400]}); print(("PASS " if passed else "FAIL ") + name, detail if not passed else "")

admin = seed_admin(); A = seed_tenant("Pipe Alpha"); B = seed_tenant("Pipe Beta")
ca, a, b = client(admin["token"]), client(A["token"], A["tenant_id"]), client(B["token"], B["tenant_id"])
for c, T in ((a, A), (b, B)): c.post(f"/crm/{T['tenant_id']}/setup")
WANT = int(os.getenv("PIPE_TARGET", "10"))
def start(T, cities, industry):
    r = ca.post("/leads/v2/pipeline/run", json={"tenant_id": T["tenant_id"], "industry": industry, "locations": cities, "target_new_leads": WANT, "limit_per_location": 15, "deliver": False, "use_crawl4ai": False, "use_firecrawl": os.getenv("PIPE_FIRECRAWL") == "1", "include_maps": True, "include_web": False})
    return r.json()
t0 = time.time()
with cf.ThreadPoolExecutor(2) as ex:
    fa = ex.submit(start, A, os.getenv("PIPE_CITIES_A", "Austin, TX;Dallas, TX;Houston, TX").split(";"), "dental"); fb = ex.submit(start, B, os.getenv("PIPE_CITIES_B", "Denver, CO;Boulder, CO;Aurora, CO").split(";"), "gym")
    ra, rb = fa.result(), fb.result()
rec("pipeline: both tenants accepted a run concurrently", bool(ra.get("run_id")) and bool(rb.get("run_id")) and ra["run_id"] != rb["run_id"], (ra, rb))
runs = {A["tenant_id"]: ra.get("run_id"), B["tenant_id"]: rb.get("run_id")}
TERMINAL = {"completed", "failed", "cancelled", "partial", "succeeded", "done", "stopped"}
state = {}
while time.time() - t0 < int(os.getenv("PIPE_TIMEOUT", "420")):
    for tid, rid in runs.items():
        row = (db("GET", f"pipeline_runs?id=eq.{rid}&select=status,discovered,enriched,assigned,error_message,meta") or [{}])[0]; state[tid] = row
    if all(str(s.get("status")) in TERMINAL for s in state.values()): break
    time.sleep(6)
for name, T in (("A", A), ("B", B)):
    s = state[T["tenant_id"]]; m = s.get("meta") or {}
    print(name, "err", (s.get("error_message")), "fail", json.dumps({k: m.get(k) for k in ("failures","stop_reason","terminal_status","assignment_blockers","remaining_gap","rounds_used")}, default=str)[:500]); print(name, s.get("status"), "discovered", s.get("discovered"), "enriched", s.get("enriched"), "assigned", s.get("assigned"), "stop", m.get("stop_reason"))
    rec(f"pipeline {name}: run reached a terminal state", str(s.get("status")) in TERMINAL, s.get("status"))
    asg = db("GET", f"lead_assignments?tenant_id=eq.{T['tenant_id']}&select=id,lead_id,status,crm_status,stage_slug,tier")
    rec(f"pipeline {name}: leads persisted + assigned to tenant ({len(asg)})", len(asg) >= 1, len(asg))
    rec(f"pipeline {name}: no leads deduplicated twice within tenant", len({x['lead_id'] for x in asg}) == len(asg))
    comp = db("GET", f"companies?tenant_id=eq.{T['tenant_id']}&select=id,name&source=neq.csv_import")
    rec(f"pipeline {name}: CRM synchronisation created companies ({len(comp)})", len(comp) >= 1 and len(comp) <= len(asg) + 1, (len(comp), len(asg)))
    sc = db("GET", f"scores?lead_id=in.({','.join(x['lead_id'] for x in asg)})&select=lead_id,total_score,tier") if asg else []
    rec(f"pipeline {name}: scores persisted for assigned leads", len(sc) == len(asg), (len(sc), len(asg)))
    gates = db("GET", f"lead_quality_gates?tenant_id=eq.{T['tenant_id']}&select=decision")
    rec(f"pipeline {name}: quality-gate decisions recorded ({len(gates)})", len(gates) >= len(asg))
    S = {"asg": asg}; globals()["S_" + name] = S
    # stage movement through the real API
    if asg:
        c = a if name == "A" else b
        stages = c.get(f"/crm/{T['tenant_id']}/stages").json(); stages = stages.get("stages") or stages if isinstance(stages, dict) else stages
        rec(f"pipeline {name}: stage list available", len(stages) >= 2, stages)
        tgt = [s for s in stages if s.get("slug") != asg[0].get("stage_slug")][0]
        r = c.post(f"/crm/{T['tenant_id']}/leads/{asg[0]['id']}/stage", json={"stage_id": tgt["id"]})
        row = db("GET", f"lead_assignments?id=eq.{asg[0]['id']}&select=stage_id,stage_slug")[0]
        rec(f"pipeline {name}: lead stage movement persisted", r.status_code == 200 and row["stage_id"] == tgt["id"], (r.status_code, r.text[:150], row))
        r2 = c.post(f"/crm/{T['tenant_id']}/leads/{asg[0]['id']}/stage", json={"stage_id": tgt["id"]})
        rec(f"pipeline {name}: repeating the same stage move is idempotent", r2.status_code == 200, r2.text[:150])
        ids = [x["id"] for x in asg[:3]]; r3 = c.post(f"/crm/{T['tenant_id']}/leads/bulk-stage", json={"assignment_ids": ids, "stage_id": stages[-1]["id"]})
        rec(f"pipeline {name}: bulk stage move", r3.status_code == 200, r3.text[:150])
        lst = c.get(f"/crm/{T['tenant_id']}/leads", params={"limit": 5}); rec(f"pipeline {name}: client lead list", lst.status_code == 200 and (lst.json().get("leads") or lst.json().get("items")), lst.text[:150])
# every tenant GET route must stay healthy once the tenant actually has pipeline data (the "temporarily unavailable" class of bugs)
spec = httpx.get(API + "/openapi.json").json()
bad = []
for path, ops in spec["paths"].items():
    if "get" not in ops or not path.startswith("/crm/{tenant_id}") or path.count("{") > 1: continue
    r = a.get(path.replace("{tenant_id}", A["tenant_id"]))
    if r.status_code >= 500: bad.append((r.status_code, path))
rec("data-bearing tenant: no CRM GET route returns 5xx", not bad, bad)
rec("data-bearing tenant: /pipeline board lists assigned leads", a.get(f"/crm/{A['tenant_id']}/pipeline").status_code == 200)
# isolation of jobs / runs / leads
aA, aB = {x["id"] for x in S_A["asg"]}, {x["id"] for x in S_B["asg"]}
rec("isolation: assignment sets are disjoint", not (aA & aB))
rec("isolation: A cannot read B's run", a.get(f"/crm/{B['tenant_id']}/pipeline-runs/{runs[B['tenant_id']]}").status_code in (403, 404))
r = a.get(f"/crm/{A['tenant_id']}/pipeline-runs/{runs[B['tenant_id']]}"); rec("isolation: B's run id not readable via A's tenant path", r.status_code in (403, 404) or runs[B["tenant_id"]] not in r.text, r.status_code)
if S_B["asg"]:
    rec("isolation: A cannot read B's lead by id", a.get(f"/crm/{A['tenant_id']}/leads/{S_B['asg'][0]['id']}").status_code in (403, 404))
    rec("isolation: A cannot move B's lead", a.post(f"/crm/{A['tenant_id']}/leads/{S_B['asg'][0]['id']}/stage", json={"stage_id": str(__import__("uuid").uuid4())}).status_code in (403, 404))
rec("isolation: A's lead list never contains B's leads", not (aB & {x.get("id") for x in (a.get(f"/crm/{A['tenant_id']}/leads", params={"limit": 200}).json().get("leads") or [])}))
rec("isolation: tenant cannot stop other tenant's run", a.post(f"/leads/v2/admin/runs/{runs[B['tenant_id']]}/stop").status_code in (401, 403))
# retries / recovery through the admin job controls
jobs = db("GET", "jobs?select=id,kind,status,attempts&kind=eq.pipeline.run")
rec("jobs: pipeline jobs recorded durably, none stuck in running", jobs and all(j["status"] != "running" for j in jobs), jobs)
if jobs:
    r = ca.post(f"/leads/v2/admin/jobs/{jobs[0]['id']}/retry"); rec("jobs: admin retry endpoint answers without 5xx", r.status_code < 500, (r.status_code, r.text[:200]))
    rec("jobs: admin job detail + list", ca.get(f"/leads/v2/admin/jobs/{jobs[0]['id']}").status_code == 200 and ca.get("/leads/v2/admin/jobs").status_code == 200)
rec("admin: scale-health reports workers", ca.get("/leads/v2/admin/scale-health").status_code == 200)
json.dump(R, open(os.getenv("E2E_OUT", "pipeline_results.json"), "w"), indent=1)
print({k: sum(1 for r in R if r["status"] == k) for k in ("PASS", "FAIL")}, "elapsed", round(time.time() - t0))
sys.exit(1 if any(r["status"] == "FAIL" for r in R) else 0)
