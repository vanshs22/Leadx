"""End-to-end: discovery -> persist -> enrich -> verify -> score -> GATE -> assign, on the strict fake DB.
Offline: discovery/enrichment fed with synthetic leads; MX lookup + SMTP are simulated. No paid APIs."""
import asyncio, sys, logging, json
sys.path.insert(0, ".")
from tests.rig.harness import *
logging.disable(logging.WARNING) if "-q" in sys.argv else None
from app.integrations.prod import continuous_discovery as cd
from app.integrations.prod import contact_verification_mesh as cvm
import app.layers.enrichment_layer as el

def mk(i, **kw):
    base = {"name": f"Glow Clinic {i}", "website": f"https://glowclinic{i}.com", "address": f"{i} Main St, Austin, TX",
            "city": "Austin", "state": "TX", "source": "osm", "category": "medspa", "google_place_id": f"pid{i}",
            "phone_normalized": f"51274601{i:02d}", "phone_e164": f"+151274601{i:02d}", "google_rating": 4.6, "review_count": 80}
    base.update(kw)
    from app.integrations.prod.utils import fingerprint
    base['fingerprint'] = fingerprint(base['name'], base.get('phone_normalized'), base.get('website')); return base

SYN = [mk(1, email="info@glowclinic1.com"), mk(2, email="hello@glowclinic2.com"),
       mk(3), mk(4, email="owner@gmail.com"), mk(5, email="contact@glowclinic5.com", phone_normalized=None, phone_e164=None),
       mk(6, email="x@mailinator.com")]

async def fake_discover(**kw):
    out = SYN[:]; SYN.clear(); return out
cd.discover_continuous = fake_discover
cvm._resolve_mx_sync = lambda d: ["mx1.google-mail-host.net"] if d.endswith("glowclinic1.com") or d.endswith("glowclinic2.com") or d.endswith("glowclinic5.com") or d == "gmail.com" or d == "mailinator.com" else []
async def fake_enrich(leads, **kw):
    for l in leads:
        l.update({"enrichment_ok": True, "services": ["botox", "laser"], "has_booking": True, "booking_platform": "mindbody", "enrichment_method": "test"})
        if not l.get("email") and l["name"].endswith("3"): l["email"] = "team@glowclinic3.com"
    return leads
el.enrich_for_pipeline = fake_enrich
import app.integrations.prod.pipeline as pl
pl.enrich_batch = fake_enrich

async def main():
    t = seed_tenant()
    DB.table("tenants").update({"allow_tier_b": True}).eq("id", t).execute()
    res = await pl.run_pipeline(tenant_id=t, industry="medspa", locations=["Austin, TX"], target_new_leads=3, max_assignments=3,
                                allow_tier_b=True, deliver=False, exhaustive_discovery=False, use_crawl4ai=False, use_firecrawl=False)
    keys = ("gate_rejections", "assignment_blockers", "status", "discovered", "enriched", "gated", "gated_ok", "assigned", "assigned_to_tenant", "error", "stop_reason", "gate_rejections", "blockers")
    print("RESULT", {k: res.get(k) for k in keys if k in res})
    for tb in ("leads_global", "scores", "lead_assignments", "lead_quality_gates", "lead_verification_observations", "pipeline_runs"):
        print(f"{tb:34} rows={len(DB.rows.get(tb, []))}")
    for l in DB.rows.get("lead_quality_gates", []): print("  gate:", l["decision"], l["reasons"])
    print("VIOLATIONS:"); [print("  !!", v[:200]) for v in dict.fromkeys(DB.violations)]
asyncio.run(main())
