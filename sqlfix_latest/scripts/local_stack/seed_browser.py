"""Create the browser-test identities in the throw-away database: a platform admin and a client owner (real passwords, real auth.users rows)."""
import json, os, sys, uuid
sys.path.insert(0, os.path.dirname(__file__))
from e2e_lib import db
import authshim
PW = "Test-Passw0rd!x"
admin_id = authshim.set_password("admin@leadx.test", PW)
db("POST", "platform_admins", json={"user_id": admin_id, "email": "admin@leadx.test", "role": "super_admin", "active": True})
t = db("POST", "tenants", json={"name": "Browser Agency", "slug": "browser-agency-" + uuid.uuid4().hex[:4], "plan": "growth", "leads_per_month_limit": 500, "email_verified_at": "2026-01-01T00:00:00Z", "email_verification_required": False})[0]
uid = authshim.set_password("client@leadx.test", PW)
db("POST", "tenant_members", json={"tenant_id": t["id"], "auth_user_id": uid, "email": "client@leadx.test", "role": "owner", "status": "active"})
out = {"password": PW, "admin_email": "admin@leadx.test", "client_email": "client@leadx.test", "tenant_id": t["id"]}
json.dump(out, open(os.environ.get("SEED_OUT", "/projects/sandbox/.tools/browser_seed.json"), "w")); print(json.dumps(out))

# data the client sees in the portal: stages + three leads (same shape the pipeline produces)
import datetime
from e2e_lib import client, jwt_for, httpx, API
c = client(jwt_for(uid, "client@leadx.test"), t["id"]); c.post(f"/crm/{t['id']}/setup")
def lead(name, city, cat, tier, score, site=None, email=None, phone=None):
    g = db("POST", "leads_global", json={"name": name, "city": city, "state": "TX", "category": cat, "website": site, "phone_normalized": phone, "phone_e164": ("+1" + phone) if phone else None, "fingerprint": uuid.uuid4().hex, "google_rating": 4.6})[0]
    a = db("POST", "lead_assignments", json={"tenant_id": t["id"], "lead_id": g["id"], "vertical": cat, "status": "new", "tier": tier, "delivered_at": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=score)).isoformat()})[0]
    db("POST", "scores", json={"lead_id": g["id"], "deterministic_score": score, "total_score": score, "tier": tier})
    if email: db("POST", "enrichment", json={"lead_id": g["id"], "email": email, "email_valid": True, "enrichment_ok": True})
    return a["id"]
out["lead_bright"] = lead("Austin Bright Dental", "Austin", "dental", "A", 85, "https://brightdental.test", "info@brightdental.test", "5125550111")
lead("Dallas Dental Care", "Dallas", "dental", "B", 60); lead("Austin Iron Gym", "Austin", "gym", "C", 30, "https://irongym.test")
json.dump(out, open(os.environ.get("SEED_OUT", "/projects/sandbox/.tools/browser_seed.json"), "w"))
