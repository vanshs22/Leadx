"""Time one target-N pipeline run on the local stack: first assignment, N-th assignment, terminal state.
usage via stack.sh:  SPEED_TARGET=100 SPEED_INDUSTRY=dentist SPEED_LOCATION="Austin, TX" python scripts/local_stack/speed_probe.py"""
import collections, json, os, time
from e2e_lib import *
N = int(os.getenv("SPEED_TARGET", "10")); IND = os.getenv("SPEED_INDUSTRY", "dentist"); LOC = os.getenv("SPEED_LOCATION", "Austin, TX")
admin = seed_admin(); T = seed_tenant("Speed Probe"); ca = client(admin["token"]); client(T["token"], T["tenant_id"]).post(f"/crm/{T['tenant_id']}/setup")
import psycopg2
_pg = psycopg2.connect(host="127.0.0.1", port=int(os.environ.get("PGPORT", "54329")), user="postgres", dbname="leadx_e2e"); _pg.autocommit = True
def _commits():
    c = _pg.cursor(); c.execute("select xact_commit from pg_stat_database where datname='leadx_e2e'"); return c.fetchone()[0]
c0 = _commits()
t0 = time.time()
r = ca.post("/leads/v2/pipeline/run", json={"tenant_id": T["tenant_id"], "industry": IND, "locations": [LOC], "target_new_leads": N, "limit_per_location": 15, "deliver": False,
                                             "use_crawl4ai": False, "use_firecrawl": False, "include_maps": True, "include_web": False}).json()
rid = r["run_id"]
if os.getenv("SPY"):  # sample the worker's stacks while the run is going (needs py-spy and ptrace)
    import subprocess
    pid = subprocess.check_output(["pgrep", "-f", "app.worker"], text=True).split()[0]
    spy = subprocess.Popen([os.path.join(os.path.dirname(os.environ.get("PY", "python3")), "py-spy"), "record", "-p", pid, "-d", os.getenv("SPY_SECONDS", "28"), "-r", "50", "-f", "raw", "-o", os.environ["SPY"], "--nonblocking"] + (["--idle"] if os.getenv("SPY_IDLE") else []))
first = full = done = None; last = (0, 0, 0)
while time.time() - t0 < int(os.getenv("SPEED_TIMEOUT", "900")):
    row = (db("GET", f"pipeline_runs?id=eq.{rid}&select=status,discovered,enriched,assigned,meta") or [{}])[0]
    a = row.get("assigned") or 0
    if a and first is None: first = time.time() - t0
    if a >= N and full is None: full = time.time() - t0
    last = (row.get("discovered"), row.get("enriched"), a)
    if str(row.get("status")) in ("completed", "failed", "cancelled", "partial", "succeeded", "done", "stopped"): done = time.time() - t0; break
    time.sleep(1)
m = (row.get("meta") or {})
import collections
_rej = collections.Counter(x["stage_reason"] for x in db("GET", f"pipeline_run_leads?run_id=eq.{rid}&stage_status=eq.rejected&select=stage_reason&limit=1000"))
print("RECON", json.dumps({k: m.get(k) for k in ("authoritative_counts","reconciliation","stop_projection","remaining_gap","failures","assignment_confirmed","queue_outcome","tranche","rounds_used")}, default=str)[:1500])
_asg = db("GET", f"lead_assignments?tenant_id=eq.{T['tenant_id']}&select=id,lead_id&limit=1000")
_board = {x["lead_id"]: x for x in db("GET", f"pipeline_run_leads?run_id=eq.{rid}&select=lead_id,stage,stage_status,stage_reason,crm_assignment_id&limit=1000")}
_miss = [(_board.get(a["lead_id"]) or {"stage": "NO_CARD"}) for a in _asg if not (_board.get(a["lead_id"]) or {}).get("crm_assignment_id")]
print("ASSIGNMENTS", len(_asg), "missing_board_link", len(_miss), collections.Counter((x.get("stage"), x.get("stage_status")) for x in _miss))
print("REJECTED", dict(_rej.most_common(8)))
_st = collections.Counter((x["stage"], x["stage_status"]) for x in db("GET", f"pipeline_run_leads?run_id=eq.{rid}&select=stage,stage_status&limit=1000"))
print("STAGES_COUNT", dict(_st))
c1 = _commits()
print("LAT", json.dumps((m.get("lane_telemetry") or {}).get("latency_samples")), json.dumps((m.get("lane_telemetry") or {}).get("fast_seed"))); print("STAGES", json.dumps(m.get("stage_timings") or (m.get("target_five") or {}).get("stage_timings") or {}))
print(json.dumps({"db_transactions": c1 - c0, "target": N, "first_assignment_s": first and round(first, 1), "all_assigned_s": full and round(full, 1), "terminal_s": done and round(done, 1), "status": row.get("status"),
                  "discovered_enriched_assigned": last, "fast_seed": (m.get("discovery_telemetry") or {}).get("fast_seed"), "stop_reason": m.get("stop_reason"),
                  "gate_rejections": m.get("gate_rejections"), "blockers": m.get("assignment_blockers")}))
