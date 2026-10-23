"""LeadX 14.0 Data & Intelligence Fabric API."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import require_tenant, TenantContext, assert_tenant_match

router=APIRouter(prefix="/crm",tags=["leadx-15-fabric"],dependencies=[Depends(require_tenant)])

class PlanRequest(BaseModel):
    vertical:str=Field(min_length=1,max_length=120)
    locations:list[str]=Field(default_factory=list,max_length=100)
    queries:list[str]=Field(default_factory=list,max_length=100)
    surfaces:list[str]=Field(default_factory=list,max_length=30)
    max_rounds:int=Field(default=8,ge=1,le=32)

class EnrichRequest(BaseModel):
    lead:dict=Field(default_factory=dict)
    fields:list[str]=Field(default_factory=lambda:["email","phone","owner_name","linkedin_url"],max_length=30)
    budget:float=Field(default=6.0,ge=0,le=100)
    region:str="global"
    vertical:str="global"

class ResearchRequest(BaseModel):
    lead:dict=Field(default_factory=dict)
    max_pages:int=Field(default=12,ge=1,le=50)
    use_firecrawl:bool=True

class VerifyRequest(BaseModel):
    field:str=Field(min_length=1,max_length=80)
    observations:list[dict]=Field(default_factory=list,max_length=100)

@router.get("/{tenant_id}/fabric/overview")
def overview(tenant_id:str,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.enrichment_mesh import enrichment_provider_catalog
    from app.integrations.prod.provider_adapters import adapter_catalog
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY as AGENT_REGISTRY
    from app.integrations.prod.discovery_catalog import DISCOVERY_SOURCES
    adapters=adapter_catalog()
    return {"ok":True,"version":"15.8.0","fabric":{"discovery_sources":len(DISCOVERY_SOURCES),"enrichment_catalog":len(enrichment_provider_catalog()),"executable_enrichment_adapters":sum(1 for x in adapters if x["executable"]),"agents":len(AGENT_REGISTRY)},"providers":adapters}

@router.post("/{tenant_id}/fabric/discovery-plan")
def discovery_plan(tenant_id:str,req:PlanRequest,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.discovery_planner import build_plan
    return {"ok":True,"plan":build_plan(req.vertical,req.locations,req.queries,req.surfaces or None,req.max_rounds).as_dict()}

@router.post("/{tenant_id}/fabric/enrich")
async def fabric_enrich(tenant_id:str,req:EnrichRequest,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    if not req.lead: raise HTTPException(400,"lead is required")
    from app.integrations.prod.intelligence_fabric import enrich_fields
    return {"ok":True,"result":await enrich_fields(req.lead,req.fields,budget=req.budget,region=req.region,vertical=req.vertical)}

@router.post("/{tenant_id}/fabric/research")
async def fabric_research(tenant_id:str,req:ResearchRequest,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    if not req.lead: raise HTTPException(400,"lead is required")
    from app.integrations.prod.intelligence_fabric import research_company
    return {"ok":True,"result":await research_company(req.lead,max_pages=req.max_pages,use_firecrawl=req.use_firecrawl)}

@router.post("/{tenant_id}/fabric/verify")
def fabric_verify(tenant_id:str,req:VerifyRequest,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.intelligence_fabric import verify_observations
    return {"ok":True,"result":verify_observations(req.observations,req.field)}


class VerifyContactRequest(BaseModel):
    field:str=Field(pattern="^(email|phone|phone_e164|phone_normalized)$")
    value:str=Field(min_length=3,max_length=320)

@router.post("/{tenant_id}/fabric/verify-contact")
async def verify_contact_endpoint(tenant_id:str,req:VerifyContactRequest,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.intelligence_fabric import verify_contact
    return {"ok":True,"result":await verify_contact(req.value,req.field)}

@router.get("/{tenant_id}/fabric/providers")
def providers(tenant_id:str,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.provider_adapters import adapter_catalog
    return {"ok":True,"providers":adapter_catalog()}

@router.get("/{tenant_id}/fabric/discovery-sources")
def discovery_sources(tenant_id:str,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.discovery_catalog import source_catalog, discovery_lane_catalog, configured_catalog, plan_discovery_lanes
    sources=source_catalog(); lanes=discovery_lane_catalog(); configured=configured_catalog()
    by_mode={m:sum(1 for x in sources if x.get("mode")==m) for m in {x.get("mode") for x in sources}}
    planner=plan_discovery_lanes(25, rotation_key=tenant_id)
    return {"ok":True,"sources":sources,"source_count":len(sources),"lane_count":len(lanes),"configured_count":len(configured),"mode_counts":by_mode,"planner":{"exhaustive_default":False,"target_tranches":{"tiny":plan_discovery_lanes(25, rotation_key=tenant_id)["tranche_size"],"bounded":plan_discovery_lanes(500, rotation_key=tenant_id)["tranche_size"],"large":plan_discovery_lanes(5000, rotation_key=tenant_id)["tranche_size"]},"sample":planner},"execution_order":["free_open_local_web","serper_search_maps_gap_fill","configured_connector_and_paid_providers"]}

@router.get("/{tenant_id}/fabric/enrichment-sources")
def enrichment_sources(tenant_id:str,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.enrichment_mesh import enrichment_provider_catalog
    rows=enrichment_provider_catalog()
    return {"ok":True,"providers":rows,"provider_count":len(rows),"execution_order":["website_first_party_scrapling_crawl4ai","configured_free_plan","configured_paid_waterfall"],"free_first":True}
