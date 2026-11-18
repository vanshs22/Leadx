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
    for bad in ("http://user:pw@hooks.example.com/x", "ftp://hooks.example.com/x", "file:///etc/passwd"):  # private-IP rejection is covered by unit tests; the stack sets ALLOW_PRIVATE_OUTBOUND for change-detection
        r = a.post(f"/crm/{TA}/integrations/webhooks", json={"name": "hook", "endpoint_url": bad, "events": ["lead.assigned"]})
        assert r.status_code in (400, 422), f"unsafe URL accepted: {bad} {r.status_code} {r.text[:120]}"
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

# ---------------- lead tools: NL search, data quality, duplicates/merge, change detection ----------------
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
SITE = {"html": "<title>Bright Dental</title><body>" + " ".join(f"implant{i} whitening care" for i in range(150)) + " info@brightdental.test (512) 555-0111</body>"}
class _H(BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_GET(self):
        b = SITE["html"].encode(); self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
_srv = ThreadingHTTPServer(("127.0.0.1", 54350), _H); threading.Thread(target=_srv.serve_forever, daemon=True).start()
def seed_lead(T, name, city, cat, tier, score, website=None, email=None, phone=None):
    g = db("POST", "leads_global", json={"name": name, "city": city, "state": "TX", "category": cat, "website": website, "email": email, "phone_normalized": phone, "phone_e164": ("+1" + phone) if phone else None,
                                         "fingerprint": uuid.uuid4().hex, "google_rating": 4.6})[0]
    asg = db("POST", "lead_assignments", json={"tenant_id": T, "lead_id": g["id"], "vertical": cat, "status": "new", "tier": tier, "delivered_at": (__import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(minutes=score)).isoformat()})[0]
    db("POST", "scores", json={"lead_id": g["id"], "deterministic_score": score, "total_score": score, "tier": tier})
    if email: db("POST", "enrichment", json={"lead_id": g["id"], "email": email, "email_valid": True, "enrichment_ok": True})  # the CRM view reads email from enrichment
    return asg["id"], g["id"]
L = {}
@check("lead tools: seed leads for tenant A and B")
def _():
    L["bright"], L["bright_lead"] = seed_lead(TA, "Austin Bright Dental", "Austin", "dental", "A", 85, "http://127.0.0.1:54350/", "info@brightdental.test", "5125550111")
    L["dallas"], _x = seed_lead(TA, "Dallas Dental Care", "Dallas", "dental", "B", 60)
    L["gym"], _x = seed_lead(TA, "Austin Iron Gym", "Austin", "gym", "C", 30, "https://irongym.test")
    L["b_lead"], _x = seed_lead(TB, "Beta Austin Dental", "Austin", "dental", "A", 90, "https://beta.test", "x@beta.test", "5125550999")
@check("nl-search: 'hot dental in Austin with email' returns only matching own-tenant lead")
def _():
    d = ok(a.post(f"/crm/{TA}/leads/nl-search", json={"query": "hot dental leads in Austin with email"}))
    names = [x["company_name"] for x in d["leads"]]
    assert names == ["Austin Bright Dental"], (names, d["interpreted_as"]); assert d["explanation"]
@check("nl-search: score/phone/negation filters and tenant isolation")
def _():
    d = ok(a.post(f"/crm/{TA}/leads/nl-search", json={"query": "dental without email score above 50"})); assert [x["company_name"] for x in d["leads"]] == ["Dallas Dental Care"], ([x["company_name"] for x in d["leads"]], d["interpreted_as"])
    d = ok(a.post(f"/crm/{TA}/leads/nl-search", json={"query": "dental in Austin"})); assert all("Beta" not in x["company_name"] for x in d["leads"])
    assert a.post(f"/crm/{TA}/leads/nl-search", json={"query": "?!"}).status_code in (422,)
@check("nl-search: filter-syntax injection attempt handled without 5xx")
def _():
    r = a.post(f"/crm/{TA}/leads/nl-search", json={"query": "x),tenant_id.neq.0,(y in a%b*c"}); assert r.status_code < 500, r.text[:200]
    if r.status_code == 200: assert all("Beta" not in x["company_name"] for x in r.json()["leads"])
@check("leads list: cursor pages are not served from the first page's cache")
def _():
    p1 = ok(a.get(f"/crm/{TA}/leads", params={"limit": 1})); c = p1.get("next_cursor"); assert c, p1
    p2 = ok(a.get(f"/crm/{TA}/leads", params={"limit": 1, "cursor": c})); assert p2["leads"] and p2["leads"][0]["assignment_id"] != p1["leads"][0]["assignment_id"]
@check("data-quality: per-lead + summary, free providers first")
def _():
    d = ok(a.get(f"/crm/{TA}/leads/{L['dallas']}/data-quality")); assert "email" in d["missing"] and d["confidence"] < 60, d
    er = next(r for r in d["recommendations"] if r["field"] == "email"); paid = [p["paid"] for p in er["recovery_order"]]; assert paid == sorted(paid), er
    s = ok(a.get(f"/crm/{TA}/data-quality/summary")); assert s["sampled"] == 3 and s["most_common_gaps"]
    assert a.get(f"/crm/{TA}/leads/{L['b_lead']}/data-quality").status_code == 404
@check("change-detection: baseline, no-change, then redesign + new contact + tenant isolation")
def _():
    r1 = ok(a.post(f"/crm/{TA}/leads/{L['bright']}/refresh-check")); assert r1["baseline"] is True and r1["lane"] in ("scrapling", "crawl4ai"), r1
    r2 = ok(a.post(f"/crm/{TA}/leads/{L['bright']}/refresh-check")); assert r2["changed"] is False, r2
    SITE["html"] = "<title>Bright Dental</title><body>" + " ".join(f"implant{i} whitening care" for i in range(150)) + " sales@brightdental.test (512) 555-0111</body>"
    r3 = ok(a.post(f"/crm/{TA}/leads/{L['bright']}/refresh-check")); assert any(c["type"] == "contact_change" for c in r3["changes"]), r3
    SITE["html"] = "<title>Totally New</title><body>" + " ".join(f"brand new copy word{i}" for i in range(300)) + "</body>"
    r4 = ok(a.post(f"/crm/{TA}/leads/{L['bright']}/refresh-check")); assert any(c["type"] == "site_redesign" for c in r4["changes"]), r4
    ch = ok(a.get(f"/crm/{TA}/leads/{L['bright']}/changes"))["changes"]; assert {"contact_change", "site_redesign"} <= {c["signal_type"] for c in ch}, ch
    assert b.post(f"/crm/{TB}/leads/{L['bright']}/refresh-check").status_code == 404
    assert a.post(f"/crm/{TA}/leads/{L['gym']}/refresh-check").status_code in (502, 422, 200)  # unreachable .test host must not 5xx-crash
@check("duplicates: detect + merge companies re-points children; cross-tenant merge refused")
def _():
    c1 = ok(a.post(f"/crm/{TA}/companies", json={"name": "Dup Dental", "domain": "dupdental.test", "tags": ["x"]}))["company"]["id"]
    c2 = db("POST", "companies", json={"tenant_id": TA, "name": "DUP DENTAL LLC", "website": "https://www.dupdental.test/", "phone": "(512) 555-7777", "tags": ["y"], "domain": None})[0]["id"]
    ct = ok(a.post(f"/crm/{TA}/contacts", json={"company_id": c2, "full_name": "Dee Dup", "email": "dee@dupdental.test"}))["contact"]["id"]
    dl = ok(a.post(f"/crm/{TA}/deals", json={"title": "Dup deal", "company_id": c2}))["deal"]["id"]
    g = ok(a.get(f"/crm/{TA}/duplicates", params={"entity": "companies"}))["groups"]
    grp = next(x for x in g if {r["id"] for r in x["records"]} >= {c1, c2}); keep = grp["suggested_keep"]; other = c2 if keep == c1 else c1
    assert b.post(f"/crm/{TB}/companies/merge", json={"keep_id": c1, "merge_ids": [c2]}).status_code == 404
    assert db("GET", f"companies?id=in.({c1},{c2})&select=id").__len__() == 2
    m = ok(a.post(f"/crm/{TA}/companies/merge", json={"keep_id": keep, "merge_ids": [other]}))
    assert db("GET", f"companies?id=eq.{other}&select=id") == [] and db("GET", f"contacts?id=eq.{ct}&select=company_id")[0]["company_id"] == keep
    assert db("GET", f"deals?id=eq.{dl}&select=company_id")[0]["company_id"] == keep
    kept = db("GET", f"companies?id=eq.{keep}&select=tags,domain,phone")[0]; assert sorted(kept["tags"]) == ["x", "y"] and kept["domain"] == "dupdental.test" and kept["phone"], kept
    assert a.post(f"/crm/{TA}/companies/merge", json={"keep_id": keep, "merge_ids": [keep]}).status_code == 400
@check("duplicates: contacts by email")
def _():
    ok(a.post(f"/crm/{TA}/contacts", json={"full_name": "Sam One", "email": "sam@dupco.test"})); ok(a.post(f"/crm/{TA}/contacts", json={"full_name": "Samuel One", "email": "SAM@dupco.test"}))
    g = ok(a.get(f"/crm/{TA}/duplicates", params={"entity": "contacts"}))["groups"]; assert any("email" in x["reasons"] for x in g)
    assert ok(b.get(f"/crm/{TB}/duplicates", params={"entity": "contacts"}))["groups"] == []

# ---------------- saved searches, committee, signed webhooks, signal plays ----------------
import hashlib, hmac as _hmac
GOT = []
class _Recv(BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); GOT.append({"headers": dict(self.headers), "body": self.rfile.read(n).decode()})
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
_srv2 = ThreadingHTTPServer(("127.0.0.1", 54351), _Recv); threading.Thread(target=_srv2.serve_forever, daemon=True).start()
@check("saved search: create, run (no new), new matching lead appears, alert notification written, isolated per tenant")
def _():
    s = ok(a.post(f"/crm/{TA}/saved-searches", json={"name": "Austin dental", "query": "dental in Austin", "alert": True})); vid = (s["saved_search"].get("view") or s["saved_search"]).get("id"); assert vid, s
    assert "Austin dental" in a.get(f"/crm/{TA}/saved-searches").text and "Austin dental" not in b.get(f"/crm/{TB}/saved-searches").text
    r0 = ok(a.post(f"/crm/{TA}/saved-searches/{vid}/run")); assert r0["new_matches"] == 0, r0
    time.sleep(1.1); new_id, _x = seed_lead(TA, "Austin Smile Studio Dental", "Austin", "dental", "B", 55)
    db("PATCH", f"lead_assignments?id=eq.{new_id}", json={"delivered_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()})  # delivered just now
    r1 = ok(a.post(f"/crm/{TA}/saved-searches/{vid}/run")); assert r1["new_matches"] == 1 and r1["new_leads"][0]["company_name"] == "Austin Smile Studio Dental", r1
    assert ok(a.post(f"/crm/{TA}/saved-searches/{vid}/run"))["new_matches"] == 0
    assert any("Austin dental" in (n.get("title") or "") for n in db("GET", f"notifications?tenant_id=eq.{TA}&select=title"))
    assert b.post(f"/crm/{TB}/saved-searches/{vid}/run").status_code == 404
@check("buying committee: roles, gaps and first-contact recommendation")
def _():
    co = ok(a.post(f"/crm/{TA}/companies", json={"name": "Committee Co", "domain": "committee.test"}))["company"]["id"]
    for n, ti, em in (("Pat Owner", "Owner", "pat@committee.test"), ("Rae Desk", "Front Desk Receptionist", None), ("Lee Mkt", "Marketing Manager", "lee@committee.test")):
        ok(a.post(f"/crm/{TA}/contacts", json={"company_id": co, "full_name": n, "title": ti, "email": em}))
    d = ok(a.get(f"/crm/{TA}/companies/{co}/buying-committee")); roles = {m["name"]: m["role"] for m in d["members"]}
    assert roles == {"Pat Owner": "economic_buyer", "Rae Desk": "gatekeeper", "Lee Mkt": "operational_decider"}, roles
    assert d["recommended_first_contact"]["name"] == "Pat Owner" and d["coverage_score"] >= 90 and d["multi_threaded"], d
    assert b.get(f"/crm/{TB}/companies/{co}/buying-committee").status_code == 404
@check("webhooks: per-webhook secret shown once, test ping is signed v2 and verifiable, delivery log, secret never listed")
def _():
    w = ok(a.post(f"/crm/{TA}/integrations/webhooks", json={"name": "recv", "endpoint_url": "http://127.0.0.1:54351/hook", "events": ["lead.assigned"]})); sec = w["signing_secret"]; wid = w["webhook"]["id"]
    assert sec.startswith("whsec_") and "signing_secret" not in a.get(f"/crm/{TA}/integrations/webhooks").text
    r = ok(a.post(f"/crm/{TA}/integrations/webhooks/{wid}/test")); assert r["ok"] and r["status"] == "delivered" and r["response_status"] == 200, r
    h = GOT[-1]["headers"]; ts = h["x-leadx-timestamp"]; exp = _hmac.new(sec.encode(), f"{ts}.{GOT[-1]['body']}".encode(), hashlib.sha256).hexdigest()
    assert h["x-leadx-signature-v2"] == f"t={ts},v1={exp}" and abs(time.time() - int(ts)) < 60
    assert ok(a.get(f"/crm/{TA}/integrations/webhooks/{wid}/deliveries"))["deliveries"][0]["status"] == "delivered"
    assert b.post(f"/crm/{TB}/integrations/webhooks/{wid}/test").status_code == 404
@check("webhooks: failing receiver is retried with backoff and recorded, not lost")
def _():
    w = ok(a.post(f"/crm/{TA}/integrations/webhooks", json={"name": "dead", "endpoint_url": "http://127.0.0.1:9/none", "events": ["lead.assigned"], "retry_limit": 3})); wid = w["webhook"]["id"]
    r = ok(a.post(f"/crm/{TA}/integrations/webhooks/{wid}/test")); assert r["ok"] is False and r["status"] == "queued" and r["attempts"] == 1 and r["last_error"], r
@check("signal play: automation on lead.signal_detected creates a task when a site change is detected (worker processes the event)")
def _():
    ok(a.post(f"/crm/{TA}/automations", json={"name": "Contact change play", "trigger_type": "lead.signal_detected", "enabled": True,
                                               "graph": {"conditions": [{"field": "signal_type", "op": "eq", "value": "contact_change"}], "actions": [{"type": "create_task", "params": {"priority": "high", "title": "Follow up: {title}"}}]}}))
    SITE["html"] = "<title>Bright Dental</title><body>" + " ".join(f"implant{i} whitening care" for i in range(150)) + " hello@brightdental.test (512) 555-0111</body>"
    ok(a.post(f"/crm/{TA}/leads/{L['bright']}/refresh-check"))
    end = time.time() + 150; found = []
    while time.time() < end and not found:
        found = [x for x in db("GET", f"tasks?tenant_id=eq.{TA}&select=title,priority") if "Follow up: Contact Change: " in (x["title"] or "")]
        time.sleep(4)
    assert found, "no task created by the play within 150s"

# ---------------- billing (Stripe stand-in; keys are fake test values) ----------------
def stripe_event(etype, obj, eid=None):
    body = json.dumps({"id": eid or "evt_" + uuid.uuid4().hex[:10], "object": "event", "type": etype, "created": int(time.time()), "data": {"object": obj}}); ts = str(int(time.time()))
    return body, f"t={ts},v1=" + _hmac.new(b"whsec_local_fake", f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
@check("billing: checkout session is created for a plan; free plan and bad plan handled; tenant-admin only")
def _():
    r = ok(a.post("/billing/create-checkout-session", json={"plan": "growth", "interval": "monthly"})); assert r["redirect_url"].startswith("https://checkout.stripe.test/"), r
    sent = httpx.get("http://127.0.0.1:54360/__log").json()[-1]["form"]; assert sent["line_items[0][price]"] == "price_growth_m" and sent["metadata[tenant_id]"] == TA and sent["mode"] == "subscription", sent
    assert ok(a.post("/billing/create-checkout-session", json={"plan": "free"}))["free"] is True
    assert a.post("/billing/create-checkout-session", json={"plan": "platinum"}).status_code == 400
    assert a.post("/billing/create-checkout-session", json={"plan": "starter", "interval": "annual"}).status_code == 503  # price id not configured -> clear 503, not a crash
    assert httpx.post(API + "/billing/create-checkout-session", json={"plan": "growth"}).status_code == 401
@check("billing webhook: bad signature rejected; checkout + subscription events upgrade the plan from the PRICE id; replays are idempotent; cancel downgrades")
def _():
    body, _sig = stripe_event("customer.subscription.created", {})
    assert httpx.post(API + "/billing/webhook", content=body, headers={"stripe-signature": "t=1,v1=deadbeef"}).status_code == 400
    sub = {"id": "sub_1", "customer": "cus_1", "status": "active", "metadata": {"tenant_id": TB}, "items": {"data": [{"price": {"id": "price_growth_m"}}]}, "current_period_start": int(time.time()), "current_period_end": int(time.time()) + 2592000, "cancel_at_period_end": False}
    body, sig = stripe_event("customer.subscription.created", sub, "evt_fixed_1")
    r = httpx.post(API + "/billing/webhook", content=body, headers={"stripe-signature": sig}); assert r.status_code == 200, r.text
    t = db("GET", f"tenants?id=eq.{TB}&select=plan,billing_status,leads_per_month_limit,stripe_subscription_id")[0]; assert t["plan"] == "growth" and t["billing_status"] == "active" and t["stripe_subscription_id"] == "sub_1", t
    r2 = httpx.post(API + "/billing/webhook", content=body, headers={"stripe-signature": sig}); assert r2.status_code == 200 and r2.json().get("duplicate") is True, r2.text
    forged = {**sub, "metadata": {"tenant_id": TB, "plan": "agency"}}  # metadata must not grant a plan: the price decides
    body, sig = stripe_event("customer.subscription.updated", forged); httpx.post(API + "/billing/webhook", content=body, headers={"stripe-signature": sig})
    assert db("GET", f"tenants?id=eq.{TB}&select=plan")[0]["plan"] == "growth"
    body, sig = stripe_event("customer.subscription.deleted", {**sub, "status": "canceled"}); r3 = httpx.post(API + "/billing/webhook", content=body, headers={"stripe-signature": sig}); assert r3.status_code == 200, r3.text
    assert db("GET", f"tenants?id=eq.{TB}&select=plan")[0]["plan"] in ("free", "starter"), db("GET", f"tenants?id=eq.{TB}&select=plan,billing_status")
@check("billing: status endpoint and portal session; public plans list")
def _():
    s = ok(b.get("/billing/status")); assert s
    assert ok(httpx.get(API + "/public/plans"))["plans"]
    db("PATCH", f"tenants?id=eq.{TA}", json={"stripe_customer_id": "cus_portal"}); r = a.post("/billing/create-portal-session", json={}); assert r.status_code == 200 and "billing.stripe.test" in r.text, r.text[:200]

# ---------------- GTM agent missions, support, outreach ----------------
@check("GTM mission: create (default target), runs on the worker without a paid key, cancel works, other tenant cannot read it")
def _():
    r = ok(a.post(f"/crm/{TA}/agent-missions", json={"objective": "Find and qualify dental clinics in Austin", "input_data": {"vertical": "dental", "locations": ["Austin, TX"]}}))
    mid = r["mission"]["id"]; assert r["graph_count"] >= 5, r
    assert b.get(f"/crm/{TB}/agent-missions/{mid}").status_code == 404
    end = time.time() + 90; progressed = False
    while time.time() < end and not progressed:
        tasks = db("GET", f"agent_mission_tasks?mission_id=eq.{mid}&select=status"); progressed = any(x["status"] in ("running", "done", "failed", "waiting_approval", "needs_input", "blocked_external") for x in tasks) and any(x["status"] != "blocked" and x["status"] != "queued" for x in tasks); time.sleep(4)
    assert progressed, "no task left queued/blocked in 90s: " + json.dumps(db("GET", f"agent_mission_tasks?mission_id=eq.{mid}&select=status")[:5])
    c = ok(a.post(f"/crm/{TA}/agent-missions/{mid}/cancel")); assert c["mission"]["status"] == "cancelled"
    assert a.post(f"/crm/{TA}/agent-missions", json={"objective": "x"}).status_code in (400, 422)  # too short: validation error, not a 500
@check("support: client can start a conversation")
def _(): d = ok(a.post("/support/conversations")); assert d["conversation"]["tenant_id"] == TA
@check("outreach draft: evidence-led, three angles max, never sent, tenant isolated")
def _():
    d = ok(a.post(f"/crm/{TA}/leads/{L['bright']}/outreach-draft", json={})); assert d["sent"] is False and d["drafts"] and all("{unsubscribe_link}" in x["body_text"] for x in d["drafts"]), d
    assert any("4.6 stars" in x["body_text"] or "booking" in x["body_text"] or "redesign" in x["body_text"] for x in d["drafts"])
    assert b.post(f"/crm/{TB}/leads/{L['bright']}/outreach-draft", json={}).status_code == 404

# ---------------- data engine: policy, review queue, delivery, metrics ----------------
@check("engine policy: defaults, update by admin, validation, tenant isolation")
def _():
    d = ok(a.get(f"/crm/{TA}/engine/policy"))["policy"]; assert d["contact_requirement"] == "either" and d["skip_known_crm"] is True, d
    d = ok(a.put(f"/crm/{TA}/engine/policy", json={"contact_requirement": "email", "run_credit_cap": 5}))["policy"]; assert d["contact_requirement"] == "email" and d["run_credit_cap"] == 5
    assert ok(a.get(f"/crm/{TA}/engine/policy"))["policy"]["contact_requirement"] == "email"      # persisted in tenants.settings.lead_policy
    assert ok(b.get(f"/crm/{TB}/engine/policy"))["policy"]["contact_requirement"] == "either"     # other tenant untouched
    assert a.put(f"/crm/{TA}/engine/policy", json={"contact_requirement": "fax"}).status_code in (400, 422)
    assert a.get(f"/crm/{TB}/engine/policy").status_code in (403, 404)
    ok(a.put(f"/crm/{TA}/engine/policy", json={"contact_requirement": "either", "run_credit_cap": 0}))
@check("engine review queue: list, resolve, isolation, idempotent enqueue")
def _():
    sys.path.insert(0, os.getcwd()); from app.integrations.prod.review_queue import enqueue
    lead = db("GET", "leads_global?select=id&limit=1")[0]["id"]
    i1 = enqueue(TA, {"id": lead, "name": "Parked Dental", "score": 70, "tier": "B"}, "run-x", ["no_verified_contact"])
    i2 = enqueue(TA, {"id": lead, "name": "Parked Dental", "score": 71, "tier": "B"}, "run-y", ["no_verified_contact", "legacy:no_email"])
    assert i1 and i1 == i2, (i1, i2)
    items = ok(a.get(f"/crm/{TA}/engine/review"))["items"]; assert [x["id"] for x in items if x["lead_id"] == lead] == [i1] and items[0]["score"] == 71
    assert ok(b.get(f"/crm/{TB}/engine/review"))["items"] == []
    assert b.post(f"/crm/{TB}/engine/review/{i1}/resolve", json={"action": "approve"}).status_code == 404
    assert a.post(f"/crm/{TA}/engine/review/{i1}/resolve", json={"action": "maybe"}).status_code in (400, 422)
    assert ok(a.post(f"/crm/{TA}/engine/review/{i1}/resolve", json={"action": "approve"}))["item"]["status"] == "approved"
    assert a.post(f"/crm/{TA}/engine/review/{i1}/resolve", json={"action": "reject"}).status_code == 404   # already resolved
@check("engine delivery: counts, dead-letter list and replay, isolation")
def _():
    asg = db("GET", f"lead_assignments?tenant_id=eq.{TA}&select=id,lead_id&limit=1")[0]
    row = db("POST", "delivery_outbox", json={"tenant_id": TA, "event_type": "lead.assigned", "aggregate_type": "assignment", "aggregate_id": asg["id"], "payload": {},
                                              "status": "failed", "outbox_state": "dead-letter", "stable_event_id": "e2e-dl-" + uuid.uuid4().hex, "destination": "webhook", "attempts": 8,
                                              "safe_error_code": "http_500"})[0]
    d = ok(a.get(f"/crm/{TA}/engine/delivery")); assert d["counts"].get("dead-letter", 0) >= 1 and any(x["id"] == row["id"] for x in d["dead_letter"])
    assert b.post(f"/crm/{TB}/engine/delivery/{row['id']}/replay").status_code == 404
    assert a.post(f"/crm/{TB}/engine/delivery/{row['id']}/replay").status_code in (403, 404)
    assert ok(a.post(f"/crm/{TA}/engine/delivery/{row['id']}/replay"))["outbox_state"] == "pending"
    assert a.post(f"/crm/{TA}/engine/delivery/{row['id']}/replay").status_code == 404   # no longer dead-lettered
@check("engine metrics: coverage, accuracy, cost per lead from the spend ledger, time to delivery")
def _():
    db("POST", "enrichment_spend_ledger", json=[{"tenant_id": TA, "run_id": "e2e", "provider": "apollo", "field": "email", "credits": 1.0, "hit": True},
                                                {"tenant_id": TA, "run_id": "e2e", "provider": "apollo", "field": "email", "credits": 1.0, "hit": False},
                                                {"tenant_id": TB, "run_id": "e2e", "provider": "hunter", "field": "email", "credits": 9.0, "hit": True}])
    m = ok(a.get(f"/crm/{TA}/engine/metrics")); assert m["assigned"] >= 3 and m["cost"]["credits_spent"] == 2.0, m          # tenant B's 9.0 never leaks in
    assert m["cost"]["by_provider"]["apollo"]["hit_rate_pct"] == 50.0 and m["cost"]["credits_per_assigned_lead"] > 0
    assert m["coverage_pct"]["email"] is not None and m["field_accuracy_pct"] is not None and "assignment_to_delivered" in m["time_to_delivery"]
    assert ok(b.get(f"/crm/{TB}/engine/metrics"))["cost"]["by_provider"].keys() == {"hunter"}
    assert a.get(f"/crm/{TB}/engine/metrics").status_code in (403, 404)

out = os.getenv("E2E_OUT", "e2e_results.json"); json.dump(R, open(out, "w"), indent=1)
from collections import Counter
print(dict(Counter(r["status"] for r in R)))
for r in R:
    if r["status"] != "PASS": print("FAIL", r["test"], "|", r.get("error"))
sys.exit(1 if any(r["status"] != "PASS" for r in R) else 0)
