"""LeadX 3.0 platform control plane: audit, security, flags, metrics and admin auth."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import os, time
from app.memory.supabase_client import get_supabase

def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()

def audit(action:str, *, actor_id:str|None=None, actor_type:str='system', tenant_id:str|None=None, resource_type:str|None=None, resource_id:str|None=None, before=None, after=None, reason=None, ip=None, user_agent=None):
    row={"actor_id":actor_id,"actor_type":actor_type,"tenant_id":tenant_id,"action":action,"resource_type":resource_type,"resource_id":resource_id,"before_state":before,"after_state":after,"reason":reason,"ip_address":ip,"user_agent":user_agent}
    try: return (_sb().table('audit_log').insert(row).execute().data or [row])[0]
    except Exception: return row

def security_event(event_type:str, severity:str='info', *, actor_id=None, tenant_id=None, ip=None, metadata=None):
    row={"event_type":event_type,"severity":severity,"actor_id":actor_id,"tenant_id":tenant_id,"ip_address":ip,"metadata":metadata or {}}
    try: return (_sb().table('security_events').insert(row).execute().data or [row])[0]
    except Exception: return row

def feature_enabled(key:str, *, tenant_id:str|None=None, default=False)->bool:
    try:
        rows=_sb().table('feature_flags').select('*').eq('key',key).limit(1).execute().data or []
        if not rows: return default
        f=rows[0]
        if not f.get('enabled'): return False
        tids=f.get('tenant_ids') or []
        if tids and tenant_id and tenant_id not in tids: return False
        pct=int(f.get('rollout_pct') or 100)
        if pct>=100: return True
        import hashlib
        seed=f'{key}:{tenant_id or "global"}'.encode()
        return int(hashlib.sha256(seed).hexdigest()[:8],16)%100 < pct
    except Exception: return default

def record_product_event(event_name:str, *, tenant_id=None, user_id=None, properties=None):
    row={"event_name":event_name,"tenant_id":tenant_id,"user_id":user_id,"properties":properties or {}}
    try: _sb().table('product_events').insert(row).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return row

def record_queue_metric(queue_name:str, queued:int, running:int, failed:int, oldest_queued_seconds:int, throughput_per_minute:float=0):
    row={"queue_name":queue_name,"queued":queued,"running":running,"failed":failed,"oldest_queued_seconds":oldest_queued_seconds,"throughput_per_minute":throughput_per_minute}
    try: _sb().table('queue_metrics').insert(row).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return row

def provider_observation(provider:str, operation:str='default', *, ok=True, latency_ms=0, cost=0, error=None):
    try:
        sb=_sb(); rows=sb.table('provider_health').select('*').eq('provider',provider).eq('operation',operation).limit(1).execute().data or []
        current=rows[0] if rows else {"provider":provider,"operation":operation,"success_count":0,"failure_count":0,"estimated_cost":0}
        current['success_count']=int(current.get('success_count') or 0)+(1 if ok else 0)
        current['failure_count']=int(current.get('failure_count') or 0)+(0 if ok else 1)
        current['latency_ms']=float(latency_ms or 0); current['estimated_cost']=float(current.get('estimated_cost') or 0)+float(cost or 0)
        current['status']='healthy' if ok else 'degraded'; current['last_error']=None if ok else str(error or '')[:500]; current['observed_at']=_now()
        sb.table('provider_health').upsert(current,on_conflict='provider,operation').execute()
        return current
    except Exception: return {"provider":provider,"operation":operation,"status":"unknown"}

def platform_summary()->dict:
    now=datetime.now(timezone.utc)
    try: workers=_sb().table('worker_heartbeats').select('*').execute().data or []
    except Exception: workers=[]
    try: providers=_sb().table('provider_health').select('*').order('observed_at',desc=True).limit(100).execute().data or []
    except Exception: providers=[]
    try: security=_sb().table('security_events').select('*').order('created_at',desc=True).limit(25).execute().data or []
    except Exception: security=[]
    stale=[]
    for w in workers:
        try:
            if (now-datetime.fromisoformat(str(w['last_seen_at']).replace('Z','+00:00'))).total_seconds()>120: stale.append(w.get('worker_id'))
        except Exception: stale.append(w.get('worker_id'))
    return {"workers":workers,"stale_workers":stale,"providers":providers,"recent_security_events":security,"checked_at":_now()}
