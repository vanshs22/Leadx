"""Revenue attribution, daily briefs, ROI and next-best-action analytics."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from app.memory.supabase_client import get_supabase

def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()

def record_event(tenant_id, event_type, *, lead_id=None, assignment_id=None, deal_id=None, source=None, metadata=None, occurred_at=None):
    row={"tenant_id":tenant_id,"event_type":event_type,"lead_id":lead_id,"assignment_id":assignment_id,"deal_id":deal_id,"source":source,"metadata":metadata or {},"occurred_at":occurred_at or _now()}
    try: return (_sb().table('revenue_funnel_events').insert(row).execute().data or [row])[0]
    except Exception: return row

def attribute_deal(tenant_id, deal_id, revenue_amount, *, lead_id=None, assignment_id=None, model='first_touch', source=None):
    row={"tenant_id":tenant_id,"deal_id":deal_id,"lead_id":lead_id,"assignment_id":assignment_id,"attribution_model":model,"revenue_amount":float(revenue_amount or 0),"weight":1,"metadata":{"source":source} if source else {}}
    try: return (_sb().table('revenue_attribution').insert(row).execute().data or [row])[0]
    except Exception: return row

def daily_brief(tenant_id, days=1):
    since=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(); sb=_sb()
    def rows(t):
        try:return sb.table(t).select('*').eq('tenant_id',tenant_id).gte('created_at',since).limit(5000).execute().data or []
        except Exception:return []
    assignments=rows('lead_assignments'); replies=rows('email_replies'); deals=rows('deals')
    positive=sum(1 for r in replies if r.get('category')=='positive'); won=[d for d in deals if d.get('status')=='won']
    return {"period_days":days,"new_assignments":len(assignments),"replies":len(replies),"positive_replies":positive,"won_deals":len(won),"revenue_won":sum(float(d.get('value') or 0) for d in won),"generated_at":_now()}

def source_roi(tenant_id, days=90):
    since=(datetime.now(timezone.utc)-timedelta(days=max(1,days))).isoformat()
    try: events=_sb().table('revenue_funnel_events').select('source,event_type,metadata').eq('tenant_id',tenant_id).gte('occurred_at',since).limit(20000).execute().data or []
    except Exception: events=[]
    out=defaultdict(lambda:{'events':0,'won':0,'revenue':0.0})
    for e in events:
        s=e.get('source') or 'unknown'; out[s]['events']+=1
        if e.get('event_type')=='won':
            out[s]['won']+=1; out[s]['revenue']+=float((e.get('metadata') or {}).get('revenue') or 0)
    return {k:v for k,v in out.items()}
