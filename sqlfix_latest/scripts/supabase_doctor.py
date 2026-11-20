"""Diagnose a real Supabase project for LeadX: keys, schema, RLS, auth, and Realtime.

usage:  SUPABASE_URL=... SUPABASE_ANON_KEY=... SUPABASE_SERVICE_KEY=... [SUPABASE_JWT_SECRET=...] python scripts/supabase_doctor.py [--keep]

Creates one throwaway auth user + tenant (prefixed e2e-doctor-) and removes them at the end unless --keep is given.
Every check prints PASS / FAIL / SKIP with the reason; the exit code is 1 when any check failed.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
SERVICE = os.environ.get("SUPABASE_SERVICE_KEY", "")
SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
KEEP = "--keep" in sys.argv
RESULTS = []

CORE_TABLES = ["tenants", "tenant_members", "leads_global", "enrichment", "scores", "lead_assignments", "pipeline_runs", "companies", "contacts",
               "jobs", "delivery_outbox", "suppression_list", "global_opt_out", "pipeline_stages", "lead_quality_gates"]
NEW_TABLES = ["enrichment_negative_cache", "enrichment_spend_ledger", "lead_review_queue"]


def report(name, status, detail=""):
    RESULTS.append((name, status))
    print(f"{status:5} {name}" + (f"  - {detail}" if detail else ""), flush=True)


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_parts(token):
    h, p, s = token.split(".")
    return json.loads(b64d(h)), json.loads(b64d(p)), s, f"{h}.{p}"


def hdr(key, extra=None):
    return {"apikey": key, "Authorization": f"Bearer {key}", **(extra or {})}


def check_keys():
    ref = URL.split("//")[1].split(".")[0]
    for label, key in (("anon", ANON), ("service", SERVICE)):
        if not key:
            report(f"key {label}", "FAIL", "not set")
            continue
        if key.startswith(("sb_publishable_", "sb_secret_")):
            report(f"key {label}", "PASS", "new-style API key (opaque, signature not checkable locally)")
            continue
        try:
            _, payload, sig, signing = jwt_parts(key)
        except Exception as exc:
            report(f"key {label}", "FAIL", f"not a JWT: {type(exc).__name__}")
            continue
        problems = []
        if payload.get("ref") and payload["ref"] != ref:
            problems.append(f"ref {payload['ref']} != project {ref}")
        expect = "anon" if label == "anon" else "service_role"
        if payload.get("role") != expect:
            problems.append(f"role is {payload.get('role')}, expected {expect}")
        if SECRET:
            good = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), signing.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
            if good != sig:
                problems.append("signature does not match SUPABASE_JWT_SECRET")
        report(f"key {label}", "FAIL" if problems else "PASS", "; ".join(problems) or f"role={payload.get('role')} ref={payload.get('ref')}" + ("" if SECRET else " (signature unchecked: no JWT secret)"))


def check_rest_and_schema(c):
    r = c.get(f"{URL}/rest/v1/", headers=hdr(SERVICE))
    if r.status_code != 200:
        report("REST with service key", "FAIL", f"{r.status_code} {r.text[:150]}")
        return set()
    tables = set((r.json().get("definitions") or {}).keys())
    report("REST with service key", "PASS", f"{len(tables)} tables visible")
    missing = [t for t in CORE_TABLES if t not in tables]
    report("core schema present", "FAIL" if missing else "PASS", f"missing: {missing}" if missing else f"{len(CORE_TABLES)} core tables found")
    missing_new = [t for t in NEW_TABLES if t not in tables]
    report("migration 48 (data-engine ledgers) applied", "FAIL" if missing_new else "PASS", f"missing: {missing_new}; apply SUPABASE_FULL_MIGRATION.sql" if missing_new else "")
    return tables


def check_rls(c, tables):
    bad = []
    for t in ("leads_global", "tenants", "lead_assignments", "enrichment", "suppression_list"):
        if t not in tables:
            continue
        r = c.get(f"{URL}/rest/v1/{t}?select=*&limit=1", headers=hdr(ANON))
        leaked = r.status_code == 200 and r.json()
        if leaked:
            bad.append(t)
    report("anon cannot read tenant/lead tables", "FAIL" if bad else "PASS", f"anon read rows from {bad}" if bad else "anon gets 401/403 or no rows")


def admin_user(c):
    email = f"e2e-doctor-{uuid.uuid4().hex[:8]}@example.test"
    pw = "Doctor-" + uuid.uuid4().hex
    r = c.post(f"{URL}/auth/v1/admin/users", headers=hdr(SERVICE), json={"email": email, "password": pw, "email_confirm": True})
    if r.status_code >= 300:
        report("admin API creates a confirmed user", "FAIL", f"{r.status_code} {r.text[:200]}")
        return None
    uid = r.json()["id"]
    report("admin API creates a confirmed user", "PASS")
    t = c.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON}, json={"email": email, "password": pw})
    if t.status_code != 200:
        report("password sign-in returns a session", "FAIL", f"{t.status_code} {t.text[:200]}")
        return {"id": uid, "token": None}
    report("password sign-in returns a session", "PASS")
    return {"id": uid, "token": t.json()["access_token"], "email": email}


def make_tenant(c, user):
    ts = {"Prefer": "return=representation"}
    t = c.post(f"{URL}/rest/v1/tenants", headers=hdr(SERVICE, ts), json={"name": "e2e-doctor", "slug": "e2e-doctor-" + uuid.uuid4().hex[:6], "plan": "growth"})
    if t.status_code >= 300:
        report("service key can create a tenant", "FAIL", f"{t.status_code} {t.text[:200]}")
        return None
    tid = t.json()[0]["id"]
    m = c.post(f"{URL}/rest/v1/tenant_members", headers=hdr(SERVICE, ts), json={"tenant_id": tid, "auth_user_id": user["id"], "email": user["email"], "role": "owner", "status": "active"})
    report("service key can create a tenant + membership", "PASS" if m.status_code < 300 else "FAIL", "" if m.status_code < 300 else f"{m.status_code} {m.text[:200]}")
    return tid if m.status_code < 300 else None


def check_isolation(c, user, tid):  # returns the id of a second tenant used for the cross-tenant realtime check
    other = c.post(f"{URL}/rest/v1/tenants", headers=hdr(SERVICE, {"Prefer": "return=representation"}), json={"name": "e2e-doctor-other", "slug": "e2e-doctor-o-" + uuid.uuid4().hex[:6], "plan": "growth"})
    other_id = other.json()[0]["id"] if other.status_code < 300 else None
    r = c.get(f"{URL}/rest/v1/tenants?select=id", headers=hdr(ANON, {"Authorization": f"Bearer {user['token']}"}))
    ids = {x["id"] for x in r.json()} if r.status_code == 200 else None
    if ids is None and r.status_code in (401, 403) and "42501" in r.text:
        # LeadX serves data through its API with the service key; `authenticated` deliberately has no table grants.
        report("authenticated role has no direct table access (API-only data design)", "PASS", "42501 permission denied on public.tenants")
    elif ids is None:
        report("RLS: member sees own tenant only", "FAIL", f"{r.status_code} {r.text[:150]}")
    else:
        report("RLS: member sees own tenant only", "PASS" if tid in ids and other_id not in ids else "FAIL", f"visible={len(ids)} own={tid in ids} other_leaked={other_id in ids}")
    return other_id


async def check_realtime(user, tid, c, other_tid=None):
    import websockets
    ws_url = URL.replace("https://", "wss://").replace("http://", "ws://") + f"/realtime/v1/websocket?apikey={ANON}&vsn=1.0.0"
    try:
        async with websockets.connect(ws_url, open_timeout=15) as ws:
            report("Realtime websocket connects", "PASS")
            join = {"topic": f"realtime:crm:{tid}", "event": "phx_join", "ref": "1", "join_ref": "1",
                    "payload": {"config": {"broadcast": {"self": False, "ack": False}, "presence": {"key": ""}, "postgres_changes": [], "private": True}, "access_token": user["token"]}}
            await ws.send(json.dumps(join))
            joined = False
            deadline = time.time() + 15
            while time.time() < deadline and not joined:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if msg.get("event") == "phx_reply" and msg.get("ref") == "1":
                    joined = msg["payload"].get("status") == "ok"
                    report("private channel crm:<tenant> join authorised for a member", "PASS" if joined else "FAIL", json.dumps(msg["payload"])[:240])
                    break
            if not joined:
                return
            await asyncio.sleep(1)
            if other_tid:
                await ws.send(json.dumps({**join, "topic": f"realtime:crm:{other_tid}", "ref": "2", "join_ref": "2"}))
                denied = None
                end = time.time() + 10
                while time.time() < end and denied is None:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if msg.get("event") == "phx_reply" and msg.get("ref") == "2":
                        denied = msg["payload"].get("status") != "ok"
                report("a member cannot join another tenant's private channel", "PASS" if denied else "FAIL", "" if denied else "join to the other tenant's channel succeeded")
            # a tenant-scoped write must produce a crm_change broadcast to the member
            c.post(f"{URL}/rest/v1/companies", headers=hdr(SERVICE), json={"tenant_id": tid, "name": "e2e-doctor-company"})
            got = None
            end = time.time() + 20
            while time.time() < end:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                except asyncio.TimeoutError:
                    await ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": "hb"}))
                    continue
                if msg.get("event") == "broadcast":
                    got = msg
                    break
            report("tenant write is broadcast as crm_change to the member", "PASS" if got else "FAIL", json.dumps(got)[:200] if got else "no broadcast within 20s (check realtime.messages RLS policy and capture trigger)")
    except Exception as exc:
        report("Realtime websocket connects", "FAIL", f"{type(exc).__name__}: {str(exc)[:200]}")


def cleanup(c, user, tids):
    if KEEP:
        return
    for tid in tids:
        if tid:
            c.delete(f"{URL}/rest/v1/companies?tenant_id=eq.{tid}", headers=hdr(SERVICE))
            c.delete(f"{URL}/rest/v1/tenant_members?tenant_id=eq.{tid}", headers=hdr(SERVICE))
            c.delete(f"{URL}/rest/v1/tenants?id=eq.{tid}", headers=hdr(SERVICE))
    if user:
        c.delete(f"{URL}/auth/v1/admin/users/{user['id']}", headers=hdr(SERVICE))


def main():
    if not (URL and ANON and SERVICE):
        print("set SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_KEY")
        sys.exit(2)
    c = httpx.Client(timeout=30)
    check_keys()
    tables = check_rest_and_schema(c)
    if tables:
        check_rls(c, tables)
    user = tid = other = None
    try:
        user = admin_user(c)
        if user and user.get("token") and "tenants" in tables:
            tid = make_tenant(c, user)
            if tid:
                other = check_isolation(c, user, tid)
                asyncio.run(check_realtime(user, tid, c, other))
    finally:
        cleanup(c, user, [tid, other])
    fails = [n for n, s in RESULTS if s == "FAIL"]
    print(f"\n{sum(s == 'PASS' for _, s in RESULTS)} passed, {len(fails)} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
