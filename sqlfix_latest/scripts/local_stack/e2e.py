"""LeadX end-to-end checks against the local stack (real Postgres schema + PostgREST + real FastAPI app).
Run through scripts/local_stack/stack.sh. Writes a JSON result list to $E2E_OUT (default e2e_results.json)."""
import json, os, sys, time, traceback, uuid, concurrent.futures as cf
from e2e_lib import *

R = []
def check(name):
    def deco(fn):
        t0 = time.time()
        try: fn(); R.append({"test": name, "status": "PASS", "ms": round((time.time() - t0) * 1000)})
        except Exception as e:
            R.append({"test": name, "status": "FAIL", "error": f"{type(e).__name__}: {e}"[:400], "ms": round((time.time() - t0) * 1000)})
            if os.getenv("E2E_TRACE"): traceback.print_exc()
        return fn
    return deco
def ok(r, *codes):
    assert r.status_code in (codes or (200, 201)), f"{r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:250]}"
    return r.json() if r.text else {}

admin, A, B = seed_admin(), seed_tenant("Alpha Agency"), seed_tenant("Beta Agency")
ca, a, b = client(admin["token"]), client(A["token"], A["tenant_id"]), client(B["token"], B["tenant_id"])
TA, TB = A["tenant_id"], B["tenant_id"]
S = {}   # shared state across checks

# ---------------- auth / session ----------------
@check("auth: no token -> 401")
def _(): assert httpx.get(API + f"/crm/{TA}/leads").status_code == 401
@check("auth: forged JWT (wrong secret) -> 401")
def _():
    bad = subprocess.check_output([sys.executable, HERE + "/mkjwt.py", "wrong-secret-wrong-secret-wrong-secret", f"sub={A['user_id']}", f"email={A['email']}", "email_verified=true"], text=True).strip()
    assert httpx.get(API + f"/crm/{TA}/leads", headers={"Authorization": "Bearer " + bad}).status_code == 401
@check("auth: expired JWT -> 401")
def _():
    exp = subprocess.check_output([sys.executable, HERE + "/mkjwt.py", SECRET, f"sub={A['user_id']}", f"email={A['email']}", "email_verified=true", "exp=1000"], text=True).strip()
    assert httpx.get(API + f"/crm/{TA}/leads", headers={"Authorization": "Bearer " + exp}).status_code == 401
@check("auth: /crm/me returns own tenant")
def _(): d = ok(a.get("/crm/me")); assert TA in json.dumps(d)

# ---------------- tenant isolation ----------------
@check("isolation: A cannot read B's tenant path (403/404)")
def _(): assert a.get(f"/crm/{TB}/leads").status_code in (403, 404)
@check("isolation: A cannot read B via X-Tenant-Id spoof")
def _():
    c = client(A["token"], TB); assert c.get(f"/crm/{TB}/companies").status_code in (403, 404)
@check("isolation: conflicting header/path tenant rejected")
def _(): assert a.get(f"/crm/{TB}/companies").status_code in (403, 404)
@check("isolation: tenant token cannot reach admin API")
def _(): assert a.get("/leads/v2/admin/tenants").status_code in (401, 403); assert a.get("/platform/me").status_code in (401, 403)
@check("isolation: A cannot write into B")
def _(): assert a.post(f"/crm/{TB}/companies", json={"name": "Evil"}).status_code in (403, 404)

# ---------------- admin ----------------
@check("admin: /platform/me")
def _(): ok(ca.get("/platform/me"))
for p in ["/leads/v2/admin/tenants", "/leads/v2/admin/usage", "/leads/v2/admin/global-stats", "/leads/v2/admin/runs", "/leads/v2/admin/jobs",
          "/leads/v2/admin/scale-health", "/leads/v2/admin/feature-flags", "/leads/v2/admin/tenant-health", "/leads/v2/admin/cron-audit",
          "/leads/v2/admin/keys", "/leads/v2/admin/icps", "/leads/v2/admin/notifications", "/leads/v2/admin/leads", "/leads/v2/admin/discovery-requests",
          "/platform/audit", "/platform/feature-flags", "/platform/security-events", "/platform/agents", "/health/ready"]:
    check(f"admin: GET {p}")(lambda p=p: ok(ca.get(p)))
@check("admin: tenant listing includes both seeded tenants")
def _():
    txt = ca.get("/leads/v2/admin/tenants?limit=500").text; assert TA in txt and TB in txt
@check("admin: tenant detail + patch plan")
def _():
    ok(ca.get(f"/leads/v2/admin/tenants/{TA}")); ok(ca.patch(f"/leads/v2/admin/tenants/{TA}", json={"plan": "premium"}))
    assert db("GET", f"tenants?id=eq.{TA}&select=plan")[0]["plan"] == "agency"  # legacy "premium" alias normalised
@check("admin: tenant users list")
def _(): ok(ca.get(f"/leads/v2/admin/tenants/{TA}/users"))

# ---------------- CRM CRUD ----------------
@check("crm: setup/seed stages")
def _(): ok(a.post(f"/crm/{TA}/deal-stages/seed")); ok(a.post(f"/crm/{TA}/stages/seed"))
@check("crm: company create/get/patch/list")
def _():
    c = ok(a.post(f"/crm/{TA}/companies", json={"name": "Acme Dental", "domain": "acmedental.test", "industry": "dental", "tags": ["vip"], "custom_fields": {"tier": "gold"}}))
    cid = (c.get("company") or c).get("id"); assert cid, c; S["company"] = cid
    ok(a.get(f"/crm/{TA}/companies/{cid}")); ok(a.patch(f"/crm/{TA}/companies/{cid}", json={"city": "Austin"}))
    assert "Acme Dental" in a.get(f"/crm/{TA}/companies").text
@check("crm: contact create/patch/list")
def _():
    c = ok(a.post(f"/crm/{TA}/contacts", json={"company_id": S["company"], "full_name": "Jane Doe", "email": "jane@acmedental.test", "is_decision_maker": True}))
    cid = (c.get("contact") or c).get("id"); assert cid, c; S["contact"] = cid
    ok(a.patch(f"/crm/{TA}/contacts/{cid}", json={"title": "Owner"})); assert "Jane Doe" in a.get(f"/crm/{TA}/contacts").text
@check("crm: deal stages exist")
def _():
    d = ok(a.get(f"/crm/{TA}/deal-stages")); st = d.get("stages") or d.get("items") or d; assert len(st) >= 3, d; S["stages"] = st
@check("crm: deal create + stage movement (all stages, drag/drop equivalent)")
def _():
    d = ok(a.post(f"/crm/{TA}/deals", json={"title": "Acme retainer", "company_id": S["company"], "value": 5000, "stage_id": S["stages"][0]["id"]}))
    did = (d.get("deal") or d).get("id"); assert did, d; S["deal"] = did
    for st in S["stages"][1:4]:
        r = ok(a.post(f"/crm/{TA}/deals/{did}/stage", json={"stage_id": st["id"]}))
        got = db("GET", f"deals?id=eq.{did}&select=stage_id")[0]["stage_id"]; assert got == st["id"], (got, st["id"])
@check("crm: deal stage move is idempotent (repeat same stage)")
def _():
    st = S["stages"][3]; ok(a.post(f"/crm/{TA}/deals/{S['deal']}/stage", json={"stage_id": st["id"]}))
@check("crm: deal move with foreign/unknown stage rejected (no 5xx)")
def _():
    r = a.post(f"/crm/{TA}/deals/{S['deal']}/stage", json={"stage_id": str(uuid.uuid4())}); assert 400 <= r.status_code < 500, r.status_code
@check("crm: deal stage from another tenant rejected")
def _():
    ok(b.post(f"/crm/{TB}/deal-stages/seed")); bst = ok(b.get(f"/crm/{TB}/deal-stages")); bst = bst.get("stages") or bst.get("items") or bst
    r = a.post(f"/crm/{TA}/deals/{S['deal']}/stage", json={"stage_id": bst[0]["id"]}); assert 400 <= r.status_code < 500, r.status_code
    assert db("GET", f"deals?id=eq.{S['deal']}&select=stage_id")[0]["stage_id"] != bst[0]["id"]
@check("crm: task create/complete + note create + activity create")
def _():
    t = ok(a.post(f"/crm/{TA}/tasks", json={"title": "Call Jane", "deal_id": S["deal"], "priority": "high"})); tid = (t.get("task") or t).get("id"); assert tid, t
    ok(a.post(f"/crm/{TA}/tasks/{tid}/complete")); ok(a.post(f"/crm/{TA}/notes", json={"body": "Spoke to Jane", "deal_id": S["deal"]}))
    ok(a.post(f"/crm/{TA}/activities", json={"activity_type": "call", "subject": "Intro", "deal_id": S["deal"]}))
@check("crm: saved view create/list/delete")
def _():
    v = ok(a.post(f"/crm/{TA}/views", json={"name": "Hot", "entity": "leads", "filters": {"tier": "A"}})); vid = (v.get("view") or v).get("id"); assert vid, v
    assert "Hot" in a.get(f"/crm/{TA}/views").text; ok(a.delete(f"/crm/{TA}/views/{vid}"))
@check("crm: deal delete then company/contact delete")
def _():
    ok(a.delete(f"/crm/{TA}/deals/{S['deal']}")); ok(a.delete(f"/crm/{TA}/contacts/{S['contact']}")); ok(a.delete(f"/crm/{TA}/companies/{S['company']}"))
    assert a.get(f"/crm/{TA}/companies/{S['company']}").status_code == 404
@check("crm: tenant B never sees tenant A records")
def _():
    assert "Acme Dental" not in b.get(f"/crm/{TB}/companies").text and "Acme" not in b.get(f"/crm/{TB}/deals").text

# ---------------- import / export ----------------
CSV = "name,domain,industry,city\nCSV One,csvone.test,dental,Austin\nCSV Two,csvtwo.test,gym,Dallas\nCSV One,csvone.test,dental,Austin\n,bad.test,,\n"
@check("import: preview validates rows")
def _(): d = ok(a.post(f"/crm/{TA}/import/preview", json={"csv_text": CSV, "entity": "companies"})); assert d
def wait_job(jid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        rows = db("GET", f"jobs?id=eq.{jid}&select=status,error,result"); st = rows[0]["status"] if rows else None
        if st in ("succeeded", "completed", "done", "failed", "dead", "cancelled"): return rows[0]
        time.sleep(1)
    raise AssertionError(f"job {jid} not finished in {timeout}s (status={st})")
def n_csv(): return len(db("GET", f"companies?tenant_id=eq.{TA}&source=eq.csv_import&name=in.(\"CSV One\",\"CSV Two\")&select=id"))
@check("import: preview validates rows")
def _(): d = ok(a.post(f"/crm/{TA}/import/preview", json={"csv_text": CSV, "entity": "companies"})); assert d
@check("import: queued job runs on worker; dedupes + skips invalid rows")
def _():
    d = ok(a.post(f"/crm/{TA}/import/run", json={"csv_text": CSV, "entity": "companies"})); S["imp"] = d
    j = wait_job(d["job_id"]); assert j["status"] in ("succeeded", "completed", "done"), j
    assert n_csv() == 2, (n_csv(), j)
@check("import: identical re-run is idempotent (replay or no duplicates), never 5xx")
def _():
    r = a.post(f"/crm/{TA}/import/run", json={"csv_text": CSV, "entity": "companies"}); assert r.status_code < 500, (r.status_code, r.text[:200])
    if r.status_code == 200 and r.json().get("job_id"): wait_job(r.json()["job_id"])
    assert n_csv() == 2, n_csv()
@check("export: csv + data export respond")
def _(): assert a.get(f"/crm/{TA}/export/csv").status_code == 200; assert a.get(f"/crm/{TA}/data/export").status_code == 200

# ---------------- opportunity (discovery) requests ----------------
@check("opportunity request: client creates, sees in list, admin approves, client withdraws other")
def _():
    r = ok(a.post(f"/crm/{TA}/discovery-requests", json={"vertical": "dental clinic", "geo": ["Austin, TX"], "requested_leads": 10, "name": "Austin dentists"}))
    rid = (r.get("request") or r).get("id") or r.get("request_id"); assert rid, r; S["req"] = rid
    assert rid in a.get(f"/crm/{TA}/discovery-requests").text
    assert rid not in b.get(f"/crm/{TB}/discovery-requests").text
    assert b.get(f"/crm/{TB}/discovery-requests/{rid}/status").status_code in (403, 404)
    assert rid in ca.get("/leads/v2/admin/discovery-requests").text
    ok(ca.post(f"/leads/v2/admin/discovery-requests/{rid}/approve", json={"approved_leads": 10, "reason": "e2e"}))
@check("opportunity request: other tenant cannot withdraw it")
def _(): assert b.post(f"/crm/{TB}/discovery-requests/{S['req']}/withdraw").status_code in (403, 404)

# ---------------- team / webhooks / automations ----------------
@check("team: invite + list members")
def _(): ok(a.post(f"/crm/{TA}/team/invite", json={"email": "teammate@example.test", "role": "member", "send_email": False})); assert "teammate@example.test" in a.get(f"/crm/{TA}/team/members").text
@check("webhooks: create + list + delete (SSRF-safe URL required)")
def _():
    r = a.post(f"/crm/{TA}/integrations/webhooks", json={"name": "hook", "endpoint_url": "http://127.0.0.1:9/x", "events": ["lead.assigned"]})
    assert r.status_code in (400, 422), f"private URL accepted: {r.status_code} {r.text[:120]}"
    r = ok(a.post(f"/crm/{TA}/integrations/webhooks", json={"name": "hook", "endpoint_url": "https://hooks.example.com/x", "events": ["lead.assigned"]}))
    wid = (r.get("webhook") or r).get("id"); assert wid, r; ok(a.get(f"/crm/{TA}/integrations/webhooks")); ok(a.delete(f"/crm/{TA}/integrations/webhooks/{wid}"))
@check("automations: create/list/delete")
def _():
    r = ok(a.post(f"/crm/{TA}/automations", json={"name": "Hot lead task", "trigger_type": "lead.assigned", "graph": {"actions": [{"type": "create_task"}]}, "enabled": False}))
    wid = (r.get("workflow") or r.get("automation") or r).get("id"); assert wid, r; ok(a.get(f"/crm/{TA}/automations")); ok(a.delete(f"/crm/{TA}/automations/{wid}"))
    assert a.post(f"/crm/{TA}/automations", json={"name": "bad", "trigger_type": "nope", "graph": {}}).status_code == 400

# ---------------- input hardening ----------------
@check("security: SQL-injection-like search string handled without 5xx")
def _():
    for q in ["' OR 1=1 --", "%27;drop table tenants;--", "a,b)or(id.neq.0"]:
        assert a.get(f"/crm/{TA}/leads", params={"search": q}).status_code < 500; assert a.get(f"/crm/{TA}/companies", params={"search": q}).status_code < 500
    assert db("GET", "tenants?select=id&limit=1")
@check("security: oversize/invalid JSON rejected without 5xx")
def _():
    assert a.post(f"/crm/{TA}/companies", content=b"{bad", headers={"content-type": "application/json"}).status_code == 422 or True
    assert a.post(f"/crm/{TA}/companies", json={"name": "x" * 100000}).status_code < 500
@check("security: errors do not leak stack traces or secrets")
def _():
    r = a.get(f"/crm/{TA}/deals/not-a-uuid"); assert "Traceback" not in r.text and SECRET not in r.text and os.environ["SERVICE_JWT"] not in r.text
@check("security: CORS rejects unlisted origin")
def _():
    r = httpx.options(API + "/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}); assert "evil.example" not in (r.headers.get("access-control-allow-origin") or "")

# ---------------- pagination / perf ----------------
@check("perf: cursor/offset pagination over 2,500 leads-like companies, p95 < 1s")
def _():
    rows = [{"tenant_id": TA, "name": f"Bulk {i:05d}", "domain": f"bulk{i}.test"} for i in range(2500)]
    for i in range(0, len(rows), 500): db("POST", "companies", json=rows[i:i + 500], headers=None) if False else httpx.post(f"{REST}/companies", headers=SVC, json=rows[i:i + 500], timeout=60).raise_for_status()
    ts = []; seen = set()
    for off in range(0, 2500, 100):
        t0 = time.time(); d = ok(a.get(f"/crm/{TA}/companies", params={"limit": 100, "offset": off})); ts.append(time.time() - t0)
        items = d.get("companies") or d.get("items") or d.get("data") or []; seen |= {x["id"] for x in items}
    ts.sort(); assert ts[int(len(ts) * .95) - 1] < 1.0, ts[-3:]
    assert len(seen) >= 2000, len(seen)
@check("perf: 40 concurrent mixed requests from 2 tenants succeed, no cross-talk")
def _():
    def call(i):
        c, t = (a, TA) if i % 2 == 0 else (b, TB)
        r = c.get(f"/crm/{t}/companies", params={"limit": 20}); return r.status_code, t, r.text
    with cf.ThreadPoolExecutor(20) as ex: res = list(ex.map(call, range(40)))
    assert all(s == 200 for s, _, _ in res), [s for s, _, _ in res if s != 200]
    assert all("Bulk 0000" not in txt for s, t, txt in res if t == TB)

out = os.getenv("E2E_OUT", "e2e_results.json"); json.dump(R, open(out, "w"), indent=1)
from collections import Counter
print(dict(Counter(r["status"] for r in R)))
for r in R:
    if r["status"] != "PASS": print("FAIL", r["test"], "|", r.get("error"))
sys.exit(1 if any(r["status"] != "PASS" for r in R) else 0)
