import asyncio, sys, json, traceback
sys.path.insert(0, ".")
from tests.rig.harness import *
from app.integrations.prod import agents as ar
from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY

def seed():
    t = seed_tenant(); lid = uid()
    DB.seed("leads_global", {"id": lid, "name": "Glow Med Spa", "fingerprint": "fp-glow-1", "website": "https://glowmedspa.example", "city": "Austin",
        "phone_normalized": "5125550101", "phone_e164": "+15125550101", "email": "info@glowmedspa.example", "category": "medspa", "source": "test"})
    DB.seed("lead_assignments", {"lead_id": lid, "tenant_id": t, "status": "new", "vertical": "medspa"})
    return t, lid

async def main():
    t, lid = seed(); out = {}
    for agent in FULL_AGENT_REGISTRY:
        before = len(DB.violations)
        try:
            run = ar.create_run(t, agent, "test", {"lead_id": lid, "lead_ids": [lid], "industry": "medspa", "locations": ["Austin, TX"], "target_new_leads": 2, "max_assignments": 2, "deliver": False})
            res = await asyncio.wait_for(ar.execute(run), 60)
            row = DB.table("agent_runs").select("*").eq("id", run["id"]).execute().data[0]
            out[agent] = ("OK" if row["status"] in ("done",) else f"STATUS={row['status']}", row.get("error"), DB.violations[before:])
        except Exception as e:
            out[agent] = ("EXC", f"{type(e).__name__}: {e}", DB.violations[before:])
    for a, (s, e, v) in out.items():
        print(f"{a:22} {s:14} {str(e)[:100] if e else ''}")
        for x in dict.fromkeys(v): print("      !!", x[:230])
asyncio.run(main())
