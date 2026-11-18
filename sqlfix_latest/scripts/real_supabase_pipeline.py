"""End-to-end pipeline test against a REAL Supabase project: real Auth, real PostgREST, real Realtime.

Starts the LeadX API and worker locally (pointed at the project), signs in two real users (a platform admin and a tenant
owner), runs a target-N pipeline, listens on the tenant's private Realtime channel the whole time, then prints the delivered
leads with their provenance and the engine metrics. Test data is prefixed e2e-real- and removed afterwards unless --keep.

usage (env: SUPABASE_URL SUPABASE_ANON_KEY SUPABASE_SERVICE_KEY SUPABASE_JWT_SECRET, optional SEARXNG_URL FIRECRAWL_API_KEY):
  python scripts/real_supabase_pipeline.py --industry dentist --location "Austin, TX" --target 10 [--keep]
Run scripts/apply_supabase_schema.py and scripts/supabase_doctor.py first.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL, ANON, SERVICE = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_ANON_KEY"], os.environ["SUPABASE_SERVICE_KEY"]
LOGS = os.environ.get("REAL_LOG_DIR", ".")  # API and worker logs land here
API_PORT = 18010
API = f"http://127.0.0.1:{API_PORT}"
SVC = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}", "Prefer": "return=representation"}
TERMINAL = {"completed", "failed", "cancelled", "partial", "succeeded", "done", "stopped"}
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  - {detail}" if detail and not ok else (f"  {detail}" if detail else "")), flush=True)


def rest(method, path, **kw):
    r = httpx.request(method, f"{URL}/rest/v1/{path}", headers=SVC, timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else None


def new_user(label):
    email, pw = f"e2e-real-{label}-{uuid.uuid4().hex[:6]}@example.test", "Real-" + uuid.uuid4().hex
    r = httpx.post(f"{URL}/auth/v1/admin/users", headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"}, json={"email": email, "password": pw, "email_confirm": True}, timeout=30)
    r.raise_for_status()
    uid = r.json()["id"]
    t = httpx.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON}, json={"email": email, "password": pw}, timeout=30)
    t.raise_for_status()
    return {"id": uid, "email": email, "token": t.json()["access_token"]}


async def listen(tenant_id, token, events, stop):
    import websockets
    ws_url = URL.replace("https://", "wss://") + f"/realtime/v1/websocket?apikey={ANON}&vsn=1.0.0"
    async with websockets.connect(ws_url, open_timeout=20) as ws:
        await ws.send(json.dumps({"topic": f"realtime:crm:{tenant_id}", "event": "phx_join", "ref": "1", "join_ref": "1",
                                  "payload": {"config": {"broadcast": {"self": False, "ack": False}, "presence": {"key": ""}, "postgres_changes": [], "private": True}, "access_token": token}}))
        last_hb = time.time()
        while not stop.is_set():
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            except asyncio.TimeoutError:
                msg = {}
            if msg.get("event") == "phx_reply" and msg.get("ref") == "1":
                events["joined"] = msg["payload"].get("status") == "ok"
            if msg.get("event") == "broadcast":
                d = (msg.get("payload") or {}).get("payload") or {}
                events["count"] = events.get("count", 0) + 1
                events.setdefault("tables", {})[d.get("table")] = events.setdefault("tables", {}).get(d.get("table"), 0) + 1
            if time.time() - last_hb > 25:
                await ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": "hb"}))
                last_hb = time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", default="dentist")
    ap.add_argument("--location", default="Austin, TX")
    ap.add_argument("--target", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    procs, users, tid = [], [], None
    env = {**os.environ, "SUPABASE_URL": URL, "SUPABASE_SERVICE_KEY": SERVICE, "SUPABASE_ANON_KEY": ANON, "ENVIRONMENT": "development",
           "LEAD_ENGINE_SERVICE_KEY": "real-run-" + uuid.uuid4().hex, "API_URL": API, "LEADX_SMTP_PROBE": "0", "SERPER_API_KEY": "", "LEADX_SEARCH_PAID_FALLBACK": "0"}
    env.setdefault("SUPABASE_JWT_SECRET", os.environ.get("SUPABASE_JWT_SECRET", ""))
    try:
        admin, owner = new_user("admin"), new_user("owner")
        users += [admin, owner]
        rest("POST", "platform_admins", json={"user_id": admin["id"], "email": admin["email"], "role": "super_admin", "active": True})
        t = rest("POST", "tenants", json={"name": "e2e-real", "slug": "e2e-real-" + uuid.uuid4().hex[:6], "plan": "growth", "leads_per_month_limit": 500,
                                          "email_verified_at": "2026-01-01T00:00:00Z", "email_verification_required": False})[0]
        tid = t["id"]
        rest("POST", "tenant_members", json={"tenant_id": tid, "auth_user_id": owner["id"], "email": owner["email"], "role": "owner", "status": "active"})
        check("real Supabase Auth: admin and tenant owner sign in", True)

        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.app_service:app", "--port", str(API_PORT), "--log-level", "warning"], cwd=ROOT, env=env,
                                      stdout=open(os.path.join(LOGS, "real_api.log"), "w"), stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen([sys.executable, "-m", "app.worker", "--concurrency", "4"], cwd=ROOT, env=env, stdout=open(os.path.join(LOGS, "real_worker.log"), "w"), stderr=subprocess.STDOUT))
        for _ in range(90):
            try:
                if httpx.get(API + "/health", timeout=3).status_code == 200:
                    break
            except Exception:
                time.sleep(1)
        ca = httpx.Client(base_url=API, headers={"Authorization": "Bearer " + admin["token"]}, timeout=120)
        co = httpx.Client(base_url=API, headers={"Authorization": "Bearer " + owner["token"], "X-Tenant-Id": tid}, timeout=120)
        me = co.get("/crm/me")
        check("API accepts the real Supabase JWT (tenant owner)", me.status_code == 200 and tid in me.text, f"{me.status_code} {me.text[:150]}")
        check("API accepts the real Supabase JWT (platform admin)", ca.get("/platform/me").status_code == 200)
        co.post(f"/crm/{tid}/setup")
        co.put(f"/crm/{tid}/engine/policy", json={"contact_requirement": "either"})

        events, stop = {}, asyncio.Event()
        loop = asyncio.new_event_loop()
        listener = None
        import threading

        def run_loop():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(listen(tid, owner["token"], events, stop))
        listener = threading.Thread(target=run_loop, daemon=True)
        listener.start()
        time.sleep(4)
        check("Realtime: tenant owner joined the private channel", events.get("joined") is True, json.dumps(events))

        started = time.time()
        r = ca.post("/leads/v2/pipeline/run", json={"tenant_id": tid, "industry": a.industry, "locations": [a.location], "target_new_leads": a.target, "limit_per_location": 15,
                                                    "deliver": False, "use_crawl4ai": False, "use_firecrawl": bool(os.environ.get("FIRECRAWL_API_KEY")), "include_maps": True, "include_web": False})
        run_id = r.json().get("run_id")
        check("pipeline run accepted", bool(run_id), r.text[:200])
        row = {}
        while time.time() - started < a.timeout:
            row = (rest("GET", f"pipeline_runs?id=eq.{run_id}&select=status,discovered,enriched,assigned,error_message,meta") or [{}])[0]
            if str(row.get("status")) in TERMINAL:
                break
            time.sleep(8)
        elapsed = round(time.time() - started)
        print(f"run {row.get('status')} discovered={row.get('discovered')} enriched={row.get('enriched')} assigned={row.get('assigned')} in {elapsed}s err={row.get('error_message')}")
        m0 = row.get("meta") or {}
        print("run meta:", json.dumps({k: m0.get(k) for k in ("stop_reason", "terminal_status", "assignment_blockers", "gate_rejections", "failures", "remaining_gap", "rounds_used")}, default=str)[:600])
        check("run reached a terminal state", str(row.get("status")) in TERMINAL, row.get("status"))
        asg = rest("GET", f"lead_assignments?tenant_id=eq.{tid}&select=id,lead_id,status,crm_status,tier,created_at")
        check(f"{a.target} leads assigned", len(asg) >= a.target, f"{len(asg)} assigned")
        ids = ",".join(x["lead_id"] for x in asg) or "00000000-0000-0000-0000-000000000000"
        leads = {x["id"]: x for x in rest("GET", f"leads_global?id=in.({ids})&select=id,name,phone_normalized,website,website_domain,source,source_provenance")}
        enr = {x["lead_id"]: x for x in rest("GET", f"enrichment?lead_id=in.({ids})&select=lead_id,email,email_valid,owner_name,enrichment_method")}
        sc = {x["lead_id"]: x for x in rest("GET", f"scores?lead_id=in.({ids})&select=lead_id,total_score,tier,reasoning")}
        contactable = [x for x in asg if (leads.get(x["lead_id"], {}).get("phone_normalized") or enr.get(x["lead_id"], {}).get("email"))]
        check("every assigned lead has a phone or email", len(contactable) == len(asg), f"{len(contactable)}/{len(asg)}")
        check("every assigned lead has a stored score with reasons", all(x["lead_id"] in sc and sc[x["lead_id"]].get("reasoning") for x in asg))
        check("no email is a URL or carries a mailto: prefix", all("/" not in (enr.get(x["lead_id"], {}).get("email") or "") and not (enr.get(x["lead_id"], {}).get("email") or "").startswith("mailto") for x in asg))
        check("CRM companies were created for the tenant", len(rest("GET", f"companies?tenant_id=eq.{tid}&select=id")) >= len(asg))
        time.sleep(3)
        check("Realtime: crm_change broadcasts reached the tenant owner during the run", events.get("count", 0) > 0, json.dumps(events)[:200])
        m = co.get(f"/crm/{tid}/engine/metrics")
        check("engine metrics endpoint answers", m.status_code == 200, m.text[:200])
        if m.status_code == 200:
            print("metrics:", json.dumps({k: m.json().get(k) for k in ("assigned", "coverage_pct", "field_accuracy_pct", "cost", "time_to_delivery", "review_queue")}, default=str)[:900])
        print("\nname | phone | email | website | sources | score")
        for x in asg:
            lg, en, s = leads.get(x["lead_id"], {}), enr.get(x["lead_id"], {}), sc.get(x["lead_id"], {})
            print(f"{(lg.get('name') or '')[:32]:32} | {lg.get('phone_normalized')} | {en.get('email')} | {lg.get('website_domain')} | {lg.get('source')} | {s.get('total_score')} {s.get('tier')}")
        stop.set()
    finally:
        for p in procs:
            p.terminate()
        if not a.keep:
            try:
                if tid:
                    for tbl in ("companies", "contacts", "lead_assignments", "tenant_members", "lead_review_queue", "enrichment_spend_ledger"):
                        try:
                            httpx.delete(f"{URL}/rest/v1/{tbl}?tenant_id=eq.{tid}", headers=SVC, timeout=60)
                        except Exception:
                            pass
                    httpx.delete(f"{URL}/rest/v1/tenants?id=eq.{tid}", headers=SVC, timeout=60)
                for u in users:
                    httpx.delete(f"{URL}/rest/v1/platform_admins?user_id=eq.{u['id']}", headers=SVC, timeout=30)
                    httpx.delete(f"{URL}/auth/v1/admin/users/{u['id']}", headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"}, timeout=30)
            except Exception as exc:
                print("cleanup incomplete:", type(exc).__name__)
    bad = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(bad)} passed, {len(bad)} failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
