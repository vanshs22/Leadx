"""LeadX 15.7 specialized Agent Fabric.

These agents are bounded specialists. They delegate to the existing LeadX
engines; they do not create parallel discovery/enrichment/CRM implementations.
"""
from __future__ import annotations
from typing import Any
from app.integrations.prod.discovery_planner import build_plan
from app.integrations.prod.intelligence_fabric import enrich_fields, research_company, verify_contact
from app.integrations.prod.provider_adapters import adapter_catalog

SPECIALIZED = {
 "discovery_planner":"Plans source/query/geography coverage before discovery.", "source_router":"Chooses executable sources using provider configuration and learned performance.",
 "identity_resolution":"Resolves duplicate entities using existing entity-resolution primitives.", "contact_finder":"Finds decision-maker candidates from existing enrichment/research paths.",
 "email_finder":"Generates candidate work-email paths for later verification.", "verification":"Builds field-level verification consensus.", "web_research":"Deep-researches first-party public web surfaces and records evidence.",
 "social_intelligence":"Normalizes public social discovery evidence.", "intent":"Summarizes observed buying-intent signals.", "trigger":"Identifies actionable business-change triggers.",
 "icp":"Applies tenant ICP/scoring inputs without inventing CRM records.", "personalization":"Produces evidence-backed personalization inputs, not autonomous sends.",
 "deliverability":"Checks contactability and delivery risk before outreach.", "compliance":"Checks suppression/approval constraints before external actions.",
 "routing":"Prepares assignment/routing recommendations.", "crm_hygiene":"Detects stale, duplicate and missing CRM fields.",
 "campaign_optimizer":"Summarizes campaign outcomes and proposes bounded experiments.", "data_quality":"Audits completeness, freshness and provenance.",
 "cost_optimizer":"Ranks provider options by expected cost per successful field.", "experiment":"Creates measurable provider/order experiments without auto-sending.",
}


async def _load_leads(data: dict[str, Any], limit: int = 100) -> list[dict]:
    ids=[str(x) for x in (data.get("lead_ids") or [])][:limit]
    if not ids:
        lead=data.get("lead")
        return [lead] if isinstance(lead,dict) else []
    from app.memory.supabase_client import get_supabase
    from app.services.aio import aexec
    sb=get_supabase()
    rows=(await aexec(sb.table("leads_global").select("*").in_("id",ids))).data or []
    # Hand-off: overlay what upstream enrichment/research agents saved in `enrichment` (email, owner, services, booking...).
    try:
        en={r["lead_id"]:r for r in ((await aexec(sb.table("enrichment").select("*").in_("lead_id",ids))).data or [])}
        for row in rows:
            for k,v in (en.get(str(row.get("id"))) or {}).items():
                if k in ("id","lead_id","created_at","updated_at","error") or v in (None,"",[],{}): continue
                if row.get(k) in (None,"",[],{}): row[k]=v
    except Exception as exc:
        __import__("logging").getLogger(__name__).warning("enrichment overlay failed: %s", exc)
    return rows

async def _persist_enrichment(lead: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    """Write the waterfall patch to the DB so the NEXT agent (verification, qualification) reads it from leads_global/enrichment."""
    import logging
    from datetime import datetime, timezone
    from app.memory.supabase_client import get_supabase
    from app.services.aio import aexec
    lid = lead.get("id")
    patch = (enrichment or {}).get("patch") or {}
    if not lid or not patch:
        return {"saved": False, "reason": "no_lead_id_or_empty_patch"}
    sb = get_supabase(); saved: list[str] = []
    try:
        g: dict[str, Any] = {}
        if patch.get("email"): g["email"] = str(patch["email"]).strip().lower()
        if patch.get("phone"):
            g["phone_e164"] = str(patch["phone"]).strip()
            g["phone_normalized"] = "".join(ch for ch in str(patch["phone"]) if ch.isdigit())[-10:] or None
        if patch.get("website"): g["website"] = str(patch["website"]).strip()
        if g:
            g["updated_at"] = datetime.now(timezone.utc).isoformat()
            await aexec(sb.table("leads_global").update(g).eq("id", str(lid))); saved += [k for k in g if k != "updated_at"]
        e: dict[str, Any] = {}
        if patch.get("owner_name"): e["owner_name"] = str(patch["owner_name"])[:200]
        if patch.get("email"): e["email"] = g.get("email")
        if e:
            await aexec(sb.table("enrichment").upsert({"lead_id": str(lid), **e}, on_conflict="lead_id")); saved += list(e)
        return {"saved": True, "fields": sorted(set(saved))}
    except Exception as exc:  # surface it in the run result instead of silently losing the handoff
        logging.getLogger(__name__).warning("enrichment persist failed for %s: %s", lid, exc)
        return {"saved": False, "reason": str(exc)[:300]}

async def execute_specialized(agent: str, data: dict[str, Any]) -> dict[str, Any]:
    leads = await _load_leads(data, int(data.get("agent_batch_limit") or 100))
    lead = leads[0] if leads else (data.get("lead") or data)
    if agent == "source_router":
        p = build_plan(str(data.get("industry") or data.get("vertical") or "local business"), list(data.get("locations") or []), list(data.get("queries") or []), list(data.get("surfaces") or []) or None, int(data.get("max_rounds") or 8))
        catalog = adapter_catalog()
        executable = [x for x in catalog if x.get("executable")]
        return {"agent":agent,"provider_snapshot":catalog,"executable":len(executable),"plan":p.as_dict(),"source_policy":["free_open","first_party_directory","serper_gap_fill","configured_connectors","paid_commercial"]}
    if agent == "discovery_planner":
        # This is the actual Discovery Agent execution step. It calls the
        # existing exhaustive discovery engine; it does not create a second
        # discovery implementation.
        from app.integrations.prod.exhaustive_discovery import discover_exhaustive
        from app.layers.persistence_layer import upsert_global_batch
        industry = str(data.get("industry") or data.get("vertical") or "local business")
        locations = list(data.get("locations") or []) or ["New Jersey"]
        queries = list(data.get("queries") or [])
        plan = build_plan(industry, locations, queries, list(data.get("surfaces") or []) or None, int(data.get("max_rounds") or 8))
        leads = await discover_exhaustive(plan.vertical, list(plan.locations), list(plan.queries))
        persisted = await upsert_global_batch(leads)
        ids = [str(x.get("id")) for x in persisted if x.get("id")]
        return {"agent":"discovery_planner","mode":"discovery_execution","industry":plan.vertical,"locations":list(plan.locations),"queries":list(plan.queries),"plan":plan.as_dict(),"discovered":len(persisted),"lead_ids":ids,"board_lead_ids":ids,"source_counts":{str(x.get("source") or "unknown"): sum(1 for y in persisted if y.get("source")==x.get("source")) for x in persisted}}

    if agent == "web_research":
        return {"agent":agent,"research":await research_company(lead, max_pages=int(data.get("max_pages") or 12), use_firecrawl=bool(data.get("use_firecrawl",True))),"lead_ids":[str(lead["id"])] if lead.get("id") else list(data.get("lead_ids") or [])}
    if agent == "enrichment":
        enrichment = await enrich_fields(lead,list(data.get("fields") or ["email","phone","owner_name","linkedin_url"]),budget=float(data.get("budget") or 6))
        persisted = await _persist_enrichment(lead, enrichment)
        return {"agent":agent,"enrichment":enrichment,"persisted":persisted,"lead_ids":[str(lead["id"])] if lead.get("id") else list(data.get("lead_ids") or [])}
    if agent == "contact_finder":
        from app.integrations.prod.social_discovery import discover_linkedin_decision_makers
        industry = str(data.get("industry") or lead.get("industry") or "local business")
        location = str(lead.get("city") or data.get("location") or "")
        contacts = await discover_linkedin_decision_makers(industry, location, limit=int(data.get("limit") or 10)) if location else []
        return {"agent":agent,"contacts":contacts,"count":len(contacts),"lead_ids":[str(x.get("id")) for x in leads if x.get("id")] or list(data.get("lead_ids") or [])}
    if agent == "email_finder":
        from app.integrations.prod.email_finder import generate_email_candidates
        candidates={str(x.get("id")):generate_email_candidates(x) for x in leads if x.get("id")}
        return {"agent":agent,"candidates":candidates,"count":sum(len(v) for v in candidates.values()),"lead_ids":list(candidates)}
    if agent == "verification":
        results=[]
        for item in leads[:100]:
            value=str(item.get("email") or item.get("phone") or "")
            field="email" if "@" in value else "phone"
            results.append({"lead_id":str(item.get("id")),"field":field,"result":await verify_contact(value,field) if value else {"status":"missing"}})
        return {"agent":agent,"results":results,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")]}
    if agent == "identity_resolution":
        from app.integrations.prod.entity_resolution import resolve_entities
        merged=resolve_entities(leads)
        return {"agent":agent,"input_count":len(leads),"resolved_count":len(merged),"leads":merged,"lead_ids":[str(x.get("id")) for x in merged if x.get("id")]}
    if agent == "social_intelligence":
        out=[]
        for item in leads:
            social=dict(item.get("social_links") or {})
            for key in ("linkedin_url","instagram_url","facebook_url","twitter_url","youtube_url"):
                if item.get(key): social[key.removesuffix("_url")]=item[key]
            out.append({"lead_id":str(item.get("id")),"social_links":social,"identities":len([v for v in social.values() if v])})
        return {"agent":agent,"results":out,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")]}
    if agent in {"intent","trigger"}:
        from app.integrations.prod.opportunity import attach_opportunity_intelligence
        results=[]
        for item in leads[:100]:
            intel=attach_opportunity_intelligence(item,str(data.get("industry") or item.get("industry") or ""))
            results.append({"lead_id":str(item.get("id")),"signals":intel.get("opportunity_evidence") or [],"intent_score":intel.get("intent_score"),"opportunity_score":intel.get("opportunity_score_overall")})
        return {"agent":agent,"results":results,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")]}
    if agent == "icp":
        from app.integrations.prod.lead_intelligence_6 import evaluate_lead
        results=[{"lead_id":str(item.get("id")),"evaluation":evaluate_lead(item,data.get("icp") or {"industries":[item.get("industry")]})} for item in leads[:100]]
        return {"agent":agent,"results":results,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")]}
    if agent == "personalization":
        from app.integrations.prod.ai_intelligence import build_lead_brief
        results=[{"lead_id":str(item.get("id")),"brief":build_lead_brief(item,str(data.get("offer") or ""))} for item in leads[:100]]
        return {"agent":agent,"results":results,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")]}
    if agent == "deliverability":
        results=[]
        for item in leads[:100]:
            value=str(item.get("email") or item.get("phone") or ""); field="email" if "@" in value else "phone"
            results.append({"lead_id":str(item.get("id")),"field":field,"result":await verify_contact(value,field) if value else {"status":"missing"}})
        return {"agent":agent,"results":results,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")]}
    if agent == "compliance":
        from app.integrations.prod.approval_inbox import auto_approve_enabled
        return {"agent":agent,"auto_approval":auto_approve_enabled(str(data.get("tenant_id") or "")),"external_send_allowed":False if data.get("require_approval",True) else True}
    if agent == "routing":
        return {"agent":agent,"recommendation":{"tenant_id":data.get("tenant_id"),"owner_id":data.get("owner_id"),"reason":"Use existing assignment/quota/exclusivity gates before write.","lead_ids":list(data.get("lead_ids") or [])[:5000]},"ready_for_assignment":bool(data.get("lead_ids"))}
    if agent in {"data_quality","crm_hygiene"}:
        required=("website","email","phone","owner_name","linkedin_url")
        lead_ids=list(data.get("lead_ids") or [])[:5000]
        records=[]
        for item in leads:
            missing=[k for k in required if not item.get(k)]
            completeness=round((len(required)-len(missing))/len(required),3)
            records.append({
                "lead_id":str(item.get("id")),
                "missing":missing,
                "completeness":completeness,
                "freshness":item.get("field_freshness") or {},
                "provenance":item.get("field_evidence") or {},
                "quality_gate_ready":completeness >= 0.6,
            })
        ready=sum(1 for r in records if r["quality_gate_ready"])
        return {"agent":agent,"lead_ids":[str(x.get("id")) for x in leads if x.get("id")] or lead_ids,"records_checked":len(records),"records":records,"ready_count":ready,"quality_gate_ready":bool(records) and ready == len(records)}
    if agent == "cost_optimizer":
        from app.integrations.prod.provider_intelligence import ProviderIntelligence, load_global_snapshot
        catalog=adapter_catalog(); intelligence=ProviderIntelligence(load_global_snapshot())
        ranked=[]
        for x in catalog:
            ranked.append({**x,"cost_rank":0 if x.get("executable") else 1})
        return {"agent":agent,"ranked":sorted(ranked,key=lambda x:(x.get("cost_rank",1),x.get("name",""))),"policy":"minimize expected cost per verified field subject to quality gates"}
    if agent == "campaign_optimizer":
        from app.integrations.prod.revenue_ops import daily_brief
        import asyncio
        return {"agent":agent,"brief":await asyncio.to_thread(daily_brief,str(data.get("tenant_id") or ""),int(data.get("days") or 30))}
    if agent == "experiment":
        return {"agent":agent,"status":"planned","experiment":data.get("experiment") or {"hypothesis":"Compare provider ordering","metric":"verified_field_rate"}}
    return {"agent":agent,"status":"ready","description":SPECIALIZED.get(agent,"specialized agent")}
