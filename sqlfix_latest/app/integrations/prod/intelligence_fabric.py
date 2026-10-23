"""LeadX 14.0 Intelligence Fabric facade.

Single entrypoint for web research, field waterfalls, verification consensus,
provider intelligence and evidence summaries. Existing pipeline remains the
source of canonical persistence/assignment.
"""
from __future__ import annotations
from typing import Any
from app.integrations.prod.provider_intelligence import ProviderIntelligence, load_global_snapshot, persist_global_snapshot
from app.integrations.prod.data_fabric import FieldObservation, fuse_observations, freshness, negative_evidence
from app.integrations.prod.provider_adapters import execute
from app.integrations.prod.waterfall import Provider, WaterfallEngine
from app.integrations.prod.enrichment_mesh import configured_enrichment_providers
from app.integrations.prod.web_intelligence import crawl_company, search_queries
from app.integrations.prod.verification_consensus import consensus
from app.integrations.prod.provider_execution import ProviderExecutionContext, execute_provider

FIELD_GROUPS={
    "email":("email",), "phone":("phone",), "person":("owner_name","linkedin_url"),
    "company":("employee_count","industry","description","founded_year","revenue"),
    "social":("linkedin_url","instagram_url","facebook_url","twitter_url","youtube_url"),
}

async def enrich_fields(lead: dict[str,Any], fields: list[str], *, budget: float=6.0, region: str="global", vertical: str="global") -> dict[str,Any]:
    providers=[]; intelligence=ProviderIntelligence(load_global_snapshot())
    context = ProviderExecutionContext.from_lead(lead, region=region, vertical=vertical, operation_kind="enrichment.waterfall")
    capability_map={"email":"email","phone":"phone","person":"owner_name","company":"employee_count","employment":"owner_name","social":"linkedin_url","verification":"email","firmographics":"employee_count","technographics":"technologies","signals":"news_signals","intent":"intent_score","location":"address","revenue":"revenue","funding":"funding"}
    for spec in configured_enrichment_providers():
        supported=tuple(dict.fromkeys([f for f in fields if f in spec.capabilities] + [capability_map[c] for c in spec.capabilities if c in capability_map and capability_map[c] in fields]))
        if not supported:
            continue
        async def fn(l, fld, _name=spec.name, _spec=spec):
            started=__import__('time').monotonic()
            call_context = context.child(field=fld, attempt=1)
            out=await execute_provider(_name, lambda: execute(_name,l,[fld]), context=call_context, timeout_s=float(getattr(_spec, "timeout_s", 30) or 30))
            latency=int((__import__('time').monotonic()-started)*1000)
            intelligence.record(_name,fld,hit=bool(out.get(fld)),latency_ms=latency,cost=_spec.cost,region=region,vertical=vertical)
            return {"value":out.get(fld),"confidence":0.72,"source_url":None} if out.get(fld) not in (None,"",[],{}) else None
        providers.append(Provider(spec.name,fn,supported,cost=spec.cost or 0.1,prior_hit_rate=0.3))
    compatible=[]
    for p in providers:
        if any((f in p.fields) or any(capability_map.get(c)==f for c in p.fields) for f in fields for _ in [0]): compatible.append(p)
    ordered=[]
    for field in fields:
        candidates=[p for p in compatible if field in p.fields or any(capability_map.get(c)==field for c in p.fields)]
        ordered.extend([p for p in intelligence.rank(candidates,field,region=region,vertical=vertical) if p not in ordered])
    engine=WaterfallEngine(ordered, budget_per_lead=budget, context=context)
    result=await engine.enrich(dict(lead),fields)
    persist_global_snapshot(intelligence.snapshot())
    return result

async def research_company(lead: dict[str,Any], *, max_pages:int=12, use_firecrawl:bool=True) -> dict[str,Any]:
    web=await crawl_company(lead.get("website") or "",max_pages=max_pages,use_firecrawl=use_firecrawl)
    domain=web.get("domain") or lead.get("website_domain") or ""
    queries=search_queries(lead.get("name") or lead.get("company_name") or "",domain,lead.get("city") or "")
    facts=web.get("facts") or {}
    return {"ok":web.get("ok",False),"facts":facts,"evidence":web.get("evidence") or [],"search_queries":queries,
            "negative_evidence":negative_evidence(web.get("web_paths_checked") or [],facts),"freshness":{k:freshness(k,None) for k in facts}}

def build_field_audit(field: str, observations: list[dict[str,Any]]) -> dict[str,Any]:
    out=fuse_observations([dict(x,field=field) for x in observations]); out["freshness"]=freshness(field, next((x.get("observed_at") for x in observations if x.get("observed_at")),None)); return out

def verify_observations(observations: list[dict[str,Any]], field: str="email") -> dict[str,Any]:
    return {"field":field,"consensus":consensus(observations),"observations":observations}

async def verify_contact(value: str, field: str) -> dict[str,Any]:
    if field == "email":
        from app.integrations.prod.contact_verification_mesh import verify_email
        result=await verify_email(value)
        observations=[]
        for obs in result.get("observations") or []:
            observations.append({**obs,"provider_class":"verification_vendor" if obs.get("provider") not in {"local_dns_disposable","local_smtp_probe","self_hosted_email_validator"} else "local_verifier"})
        return {"field":field,"value":value,"consensus":consensus(observations),"observations":observations}
    if field in {"phone","phone_e164","phone_normalized"}:
        from app.integrations.prod.contact_verification_mesh import verify_phone
        result=await verify_phone(value)
        observations=[]
        for obs in result.get("observations") or []:
            observations.append({**obs,"provider_class":"verification_vendor" if obs.get("provider") not in {"libphonenumber_local","randomapi_libphonenumber"} else "local_verifier"})
        return {"field":"phone","value":value,"consensus":consensus(observations),"observations":observations}
    raise ValueError("unsupported_verification_field")
