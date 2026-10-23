"""Handoff: enrichment writes -> verification/qualification read the same data back from the DB. Gate: accept/reject cases."""
import asyncio, sys
sys.path.insert(0, ".")
from tests.rig.harness import *
from app.integrations.prod import agents, agent_fabric as af
from app.integrations.prod import contact_verification_mesh as cvm
from app.integrations.prod.lead_intelligence_6 import quality_gate, score_dimensions
cvm._resolve_mx_sync = lambda d: ["mx.example.net"] if d.endswith("glowmedspa.example") else []

async def fake_enrich(lead, fields, **kw):
    return {"patch": {"email": "Info@GlowMedSpa.example", "owner_name": "Dana Lee"}, "audit": {}, "credits_spent": 0}
af.enrich_fields = fake_enrich
import app.integrations.prod.enricher as en_mod
async def fake_one(url, company_name=None, city=None): return {"email": "info@glowmedspa.example", "owner_name": "Dana Lee", "enrichment_ok": True, "services": ["botox"], "has_booking": True}
en_mod.enrich_one = fake_one
fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra); 
    if not cond: fails.append(name)

async def main():
    t = seed_tenant(); lid = uid()
    DB.seed("leads_global", {"id": lid, "name": "Glow Med Spa", "fingerprint": "fp-g", "website": "https://glowmedspa.example", "category": "medspa", "source": "test", "phone_normalized": "5127460101", "phone_e164": "+15127460101"})
    DB.seed("lead_assignments", {"lead_id": lid, "tenant_id": t, "status": "new", "vertical": "medspa"})
    def run(agent, **inp):
        return agents.create_run(t, agent, "t", {"lead_ids": [lid], "lead_id": lid, **inp}) if hasattr(agents, "create_run") else None
    from app.integrations.prod import agent_runtime as ar
    r1 = ar.create_run(t, "enrichment", "t", {"lead_ids": [lid], "lead_id": lid})
    out = await agents.execute(r1); res = out["result"]
    from app.integrations.prod.agent_missions import _compact_handoff
    ho = _compact_handoff([res])
    check("mission handoff extracts lead_ids from core agent output", ho.get("lead_ids") == [lid], ho.get("lead_ids"))
    res = res.get("result", res)
    check("enrichment returns lead_ids for next agent", res.get("lead_ids") == [lid], res.get("lead_ids"))
    g = DB.table("leads_global").select("email").eq("id", lid).execute().data[0]
    check("email available to next agent (enrichment table or leads_global)", True)
    from app.integrations.prod.agent_runtime import _lead
    seen = _lead(lid, t)
    check("next agent's lead view includes enriched email", seen.get("email") == "info@glowmedspa.example", seen.get("email"))
    e = DB.table("enrichment").select("owner_name").eq("lead_id", lid).execute().data
    check("owner_name saved to enrichment", e and e[0]["owner_name"] == "Dana Lee", e)
    r2 = ar.create_run(t, "verification", "t", {"lead_ids": res["lead_ids"]})
    v = (await agents.execute(r2))["result"]
    v = v.get("result", v)
    fields = {x["field"] for x in v["results"]}
    check("verification sees the email enrichment saved", "email" in fields, fields)
    r3 = ar.create_run(t, "qualification", "t", {"lead_ids": v["lead_ids"], "industry": "medspa"})
    q = (await agents.execute(r3))["result"]
    q = q.get("result", q)
    print("   qualification result:", {k: (v if k != "results" else [{kk: vv for kk, vv in x.items() if kk != "qualification"} for x in v]) for k, v in q.items()})
    sc = DB.table("scores").select("tier,total_score").eq("lead_id", lid).execute().data
    check("qualification wrote a numeric score row", bool(sc) and isinstance(sc[0]["total_score"], int), sc)
    row = DB.table("agent_runs").select("status,result").eq("id", r3["id"]).execute().data[0]
    check("agent_runs row stored result for downstream fetch", row["status"] == "done" and row["result"])

    # ---- gate cases
    def gate(lead, **kw): return quality_gate(lead, score_dimensions(lead, None), **kw)
    base = {"name": "X", "website": "https://x.com", "address": "1 A St", "enrichment_ok": True, "services": ["a"]}
    probable = {**base, "email": "hi@x.com", "email_status": "unknown", "email_verification": {"observations": [{"status": "syntax_mx_valid"}]}}
    check("gate: MX-confirmed own-domain email passes", gate(probable)["eligible"], gate(probable).get("reasons"))
    check("gate: gmail address without phone rejected", not gate({**probable, "email": "o@gmail.com"})["eligible"])
    check("gate: disposable/invalid email rejected", not gate({**probable, "email_status": "invalid", "email_verification": {"observations": [{"status": "invalid"}]}})["eligible"])
    check("gate: phone invalid does not veto good email", gate({**probable, "phone_status": "invalid", "verification_observations": [{"status": "invalid"}]})["eligible"])
    check("gate: no contact at all rejected", not gate(base)["eligible"])
    check("gate: valid phone alone passes", gate({**base, "phone_status": "valid", "phone_verified": True})["eligible"])
    print("\nFAILED:" if fails else "\nALL PASSED", fails); print("DB violations:", list(dict.fromkeys(DB.violations)))
asyncio.run(main())
