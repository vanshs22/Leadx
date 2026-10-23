from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.middleware.auth import require_tenant, TenantContext, assert_tenant_match
router=APIRouter(prefix="/crm",tags=["approval-inbox"],dependencies=[Depends(require_tenant)])
class Decision(BaseModel): approved: bool
class AutoApprove(BaseModel): enabled: bool
@router.get("/{tenant_id}/approvals")
def approvals(tenant_id:str,limit:int=Query(100,ge=1,le=500),ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.approval_inbox import list_pending,auto_approve_enabled
    return {"ok":True,"items":list_pending(tenant_id,limit),"outreach_auto_approve":auto_approve_enabled(tenant_id)}
@router.post("/{tenant_id}/approvals/settings")
def approval_settings(tenant_id:str,req:AutoApprove,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.approval_inbox import set_auto_approve
    return {"ok":True,"settings":set_auto_approve(tenant_id,req.enabled)}
@router.post("/{tenant_id}/approvals/{item_id}")
def approval_decision(tenant_id:str,item_id:str,req:Decision,ctx:TenantContext=Depends(require_tenant)):
    assert_tenant_match(ctx,tenant_id)
    from app.integrations.prod.approval_inbox import decide
    row=decide(tenant_id,item_id,req.approved,ctx.user_id)
    if not row: raise HTTPException(404,"approval_item_not_found_or_already_decided")
    return {"ok":True,"item":row}
