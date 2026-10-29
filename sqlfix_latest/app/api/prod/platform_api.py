from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from app.middleware.platform_auth import require_platform_admin, PlatformAdmin

router=APIRouter(prefix='/platform',tags=['platform-admin'])

class FlagUpdate(BaseModel):
    enabled: bool
    rollout_pct: int=Field(default=100,ge=0,le=100)
    tenant_ids:list[str]=Field(default_factory=list)
    metadata:dict=Field(default_factory=dict)

@router.get('/me')
def me(admin:PlatformAdmin=Depends(require_platform_admin)):
    return {'ok':True,'admin':admin.__dict__}

@router.get('/agents')
def agents(admin: PlatformAdmin = Depends(require_platform_admin)):
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    from app.memory.supabase_client import get_supabase
    rows = get_supabase().table('agent_runs').select('id,tenant_id,agent_id,status,created_at,started_at,finished_at,error').order('created_at', desc=True).limit(100).execute().data or []
    return {'ok': True, 'agents': [{'id': agent_id, **contract} for agent_id, contract in FULL_AGENT_REGISTRY.items()], 'runs': rows}

    from app.integrations.prod.platform_control import platform_summary
    return {'ok':True,**platform_summary()}

@router.get('/feature-flags')
def flags(admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    rows=get_supabase().table('feature_flags').select('*').order('key').execute().data or []
    return {'ok':True,'flags':rows}

@router.put('/feature-flags/{key}')
def update_flag(key:str, body:FlagUpdate, request:Request, admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    row={'key':key,'enabled':body.enabled,'rollout_pct':body.rollout_pct,'tenant_ids':body.tenant_ids,'metadata':body.metadata}
    out=get_supabase().table('feature_flags').upsert(row,on_conflict='key').execute().data or [row]
    from app.integrations.prod.platform_control import audit
    audit('feature_flag.updated',actor_id=admin.user_id,actor_type='platform_admin',resource_type='feature_flag',resource_id=key,after=row,ip=request.client.host if request.client else None)
    return {'ok':True,'flag':out[0]}

@router.get('/audit')
def audit_log(limit:int=100, admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.audit_log import safe_audit_row
    rows=get_supabase().table('audit_log').select(
        'id,tenant_id,actor_id,actor_type,action,resource_type,resource_id,reason,created_at'
    ).order('created_at',desc=True).limit(min(max(limit,1),500)).execute().data or []
    return {'ok':True,'events':[safe_audit_row(row) for row in rows]}

@router.get('/security-events')
def security_events(limit:int=100, admin:PlatformAdmin=Depends(require_platform_admin)):
    from app.memory.supabase_client import get_supabase
    from app.integrations.prod.audit_log import safe_security_event
    rows=get_supabase().table('security_events').select(
        'id,severity,event_type,actor_id,tenant_id,created_at'
    ).order('created_at',desc=True).limit(min(max(limit,1),500)).execute().data or []
    return {'ok':True,'events':[safe_security_event(row) for row in rows]}
