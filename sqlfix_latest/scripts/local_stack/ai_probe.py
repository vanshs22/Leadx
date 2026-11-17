import json
from e2e_lib import *
A = seed_tenant("AI Co"); a = client(A["token"], A["tenant_id"]); T = A["tenant_id"]
spec = httpx.get(API + "/openapi.json").json()
def req(path, m="post"):
    op = spec["paths"][path][m]; rb = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if not rb: return None
    s = rb if "$ref" not in rb else spec["components"]["schemas"][rb["$ref"].split("/")[-1]]
    return {"required": s.get("required", []), "props": list(s.get("properties", {}))[:12]}
for p in ["/crm/{tenant_id}/ai/brief", "/crm/{tenant_id}/fabric/enrich", "/crm/{tenant_id}/fabric/verify-contact", "/crm/{tenant_id}/fabric/research", "/crm/{tenant_id}/fabric/discovery-plan", "/crm/{tenant_id}/lead-intelligence/evaluate", "/crm/{tenant_id}/command", "/crm/{tenant_id}/contact-verification/email", "/crm/{tenant_id}/sequences/ai-write", "/crm/{tenant_id}/agents/run"]:
    print(p.replace("{tenant_id}", "T"), req(p))
lead = {"name": "Austin Elite Smiles", "website": "https://example.com", "city": "Austin", "industry": "dental", "phone": "+15125550101", "email": "info@example.com", "google_rating": 4.7, "review_count": 120}
calls = [("ai/brief", {"lead": lead, "offer": "lead generation"}), ("fabric/verify-contact", {"field": "email", "value": "info@example.com"}), ("fabric/discovery-plan", {"vertical": "dental", "locations": ["Austin, TX"]}),
         ("lead-intelligence/evaluate", {"lead": lead, "icp": {"vertical": "dental", "geo": ["Austin"]}}), ("command", {"command": "show me my hottest leads"}),
         ("contact-verification/email", {"email": "info@example.com"}), ("sequences/ai-write", {"industry": "dental", "goal": "book a demo"}), ("fabric/enrich", {"lead": lead, "fields": ["email", "phone"]})]
for path, body in calls:
    try: r = a.post(f"/crm/{T}/{path}", json=body); print("AI", path, r.status_code, r.text[:160].replace("\n", " "))
    except Exception as e: print("AI", path, "EXC", repr(e)[:120])
