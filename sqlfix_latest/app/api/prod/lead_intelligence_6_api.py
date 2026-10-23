from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.middleware.auth import require_tenant

router=APIRouter(prefix='/crm',tags=['lead-intelligence-6'],dependencies=[Depends(require_tenant)])

class EvaluateRequest(BaseModel):
    lead: dict=Field(default_factory=dict)
    icp: dict=Field(default_factory=dict)
    reps: list[dict]=Field(default_factory=list)

class EmailVerifyRequest(BaseModel):
    email:str
    providers:list[str]=Field(default_factory=list)

class PhoneVerifyRequest(BaseModel):
    phone:str
    country_code:str|None=None
    providers:list[str]=Field(default_factory=list)

@router.get('/{tenant_id}/lead-intelligence/capabilities')
def capabilities(tenant_id:str):
    from app.integrations.prod.lead_intelligence_6 import capabilities
    return {'version':'15.8','capabilities': capabilities() + ['canonical_intelligence_persistence','durable_lead_state_machine','assignment_audit_wiring','delivery_audit_wiring','outcome_state_wiring','schema_reconciliation']}

@router.post('/{tenant_id}/lead-intelligence/evaluate')
def evaluate(tenant_id:str,req:EvaluateRequest):
    from app.integrations.prod.lead_intelligence_6 import evaluate_lead
    return {'ok':True,'version':'15.8','result':evaluate_lead(req.lead,req.icp,req.reps)}

@router.post('/{tenant_id}/contact-verification/email')
async def verify_email(tenant_id:str,req:EmailVerifyRequest):
    from app.integrations.prod.contact_verification_mesh import verify_email
    return await verify_email(req.email,req.providers)

@router.post('/{tenant_id}/contact-verification/phone')
async def verify_phone(tenant_id:str,req:PhoneVerifyRequest):
    from app.integrations.prod.contact_verification_mesh import verify_phone
    return await verify_phone(req.phone,req.country_code,req.providers)

@router.get('/{tenant_id}/contact-verification/catalog')
def verification_catalog(tenant_id:str):
    from app.integrations.prod.contact_verification_mesh import verification_catalog
    return verification_catalog()
