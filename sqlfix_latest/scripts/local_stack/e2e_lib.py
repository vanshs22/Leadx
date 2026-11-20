"""Shared helpers for the local e2e stack: seed tenants/users via PostgREST (service role) and mint JWTs."""
import os, subprocess, sys, uuid, httpx, time
API = os.environ["API_URL"]; REST = os.environ["SUPABASE_URL"] + "/rest/v1"; SECRET = os.environ["JWT_SECRET"]
SVC = {"apikey": os.environ["SERVICE_JWT"], "Authorization": "Bearer " + os.environ["SERVICE_JWT"], "Prefer": "return=representation"}
HERE = os.path.dirname(__file__)

def jwt_for(uid, email):
    return subprocess.check_output([sys.executable, HERE + "/mkjwt.py", SECRET, f"sub={uid}", f"email={email}", "email_verified=true", "role=authenticated"], text=True).strip()

def db(method, table, **kw):
    r = httpx.request(method, f"{REST}/{table}", headers=SVC, timeout=30, **kw)
    if r.status_code >= 300: raise RuntimeError(f"{method} {table}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else None

def seed_tenant(name, plan="growth", email_domain="example.test"):
    uid = str(uuid.uuid4()); email = f"owner-{uuid.uuid4().hex[:6]}@{email_domain}"
    t = db("POST", "tenants", json={"name": name, "slug": name.lower().replace(" ", "-") + "-" + uuid.uuid4().hex[:5], "plan": plan, "leads_per_month_limit": 500, "email_verified_at": "2026-01-01T00:00:00Z", "email_verification_required": False})[0]
    db("POST", "tenant_members", json={"tenant_id": t["id"], "auth_user_id": uid, "email": email, "role": "owner", "status": "active"})
    return {"tenant_id": t["id"], "user_id": uid, "email": email, "token": jwt_for(uid, email)}

def seed_admin():
    uid = str(uuid.uuid4()); email = f"admin-{uuid.uuid4().hex[:6]}@example.test"
    db("POST", "platform_admins", json={"user_id": uid, "email": email, "role": "super_admin", "active": True})
    return {"user_id": uid, "email": email, "token": jwt_for(uid, email)}

def client(token, tenant_id=None):
    h = {"Authorization": "Bearer " + token}
    if tenant_id: h["X-Tenant-Id"] = tenant_id
    return httpx.Client(base_url=API, headers=h, timeout=60)
