"""Evidence, lineage and opportunity graph helpers."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from app.memory.supabase_client import get_supabase

def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()

def record_lineage(entity_type, entity_id, field_name, value, source, *, source_url=None, confidence=None, tenant_id=None, metadata=None):
    value_hash=sha256(str(value).encode('utf-8')).hexdigest()
    row={"tenant_id":tenant_id,"entity_type":entity_type,"entity_id":str(entity_id),"field_name":field_name,"value_hash":value_hash,"source":source,"source_url":source_url,"confidence":confidence,"metadata":metadata or {}}
    try: _sb().table('data_lineage').insert(row).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return row

def record_signal(lead_id, signal_type, strength, confidence, source, evidence, *, tenant_id=None, expires_days=None):
    row={"tenant_id":tenant_id,"lead_id":lead_id,"signal_type":signal_type,"strength":max(0,min(100,float(strength))),"confidence":max(0,min(1,float(confidence))),"source":source,"evidence":evidence or {}}
    if expires_days is not None: row['expires_at']=(datetime.now(timezone.utc)+timedelta(days=expires_days)).isoformat()
    try: return (_sb().table('lead_signals').insert(row).execute().data or [row])[0]
    except Exception: return row

def record_opportunity(lead_id, opportunity_type, scores, *, tenant_id=None, recipe_id=None, why_now=None, action=None, evidence=None, confidence=0.5, model_version='leadx-opportunity-v2'):
    vals={k:round(float(scores.get(k,0) or 0),2) for k in ('fit','need','timing','contactability','evidence','overall')}
    row={"tenant_id":tenant_id,"lead_id":lead_id,"recipe_id":recipe_id,"opportunity_type":opportunity_type,"fit_score":vals['fit'],"need_score":vals['need'],"timing_score":vals['timing'],"contactability_score":vals['contactability'],"evidence_score":vals['evidence'],"overall_score":vals['overall'],"confidence":confidence,"why_now":why_now or [],"recommended_action":action,"evidence":evidence or [],"model_version":model_version}
    try: return (_sb().table('lead_opportunities').insert(row).execute().data or [row])[0]
    except Exception: return row

def add_next_best_action(entity_type, entity_id, action, reason, *, tenant_id=None, priority=0, confidence=0.5, due_at=None):
    row={"tenant_id":tenant_id,"entity_type":entity_type,"entity_id":str(entity_id),"action":action,"reason":reason,"priority":priority,"confidence":confidence,"due_at":due_at}
    try: return (_sb().table('next_best_actions').insert(row).execute().data or [row])[0]
    except Exception: return row

def lead_timeline(lead_id, tenant_id=None, limit=100):
    events=[]
    for table, typ, date_field in [('lead_signals','signal','observed_at'),('company_change_events','change','observed_at'),('revenue_funnel_events','revenue','occurred_at')]:
        try:
            q=_sb().table(table).select('*').eq('lead_id',lead_id).order(date_field,desc=True).limit(limit)
            if tenant_id: q=q.eq('tenant_id',tenant_id)
            for r in q.execute().data or []: events.append({"type":typ,"at":r.get(date_field),"data":r})
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return sorted(events,key=lambda x:x.get('at') or '',reverse=True)[:limit]


def record_field_evidence(lead_id: str, fields: dict, *, tenant_id=None, source="leadx", source_url=None, confidence=0.5, metadata=None):
    """Record every derived field through the evidence graph.

    This is additive and best-effort so evidence persistence never blocks the
    canonical lead write. Values are individually addressable for UI "why this
    lead" explanations.
    """
    rows=[]
    for field, value in (fields or {}).items():
        if value in (None, "", [], {}):
            continue
        rows.append({"tenant_id":tenant_id,"entity_type":"lead","entity_id":str(lead_id),
                     "field_name":str(field),"value_hash":sha256(str(value).encode("utf-8")).hexdigest(),
                     "source":source,"source_url":source_url,"confidence":float(confidence),
                     "metadata":{**(metadata or {}),"value":value}})
    if not rows: return []
    try:
        return _sb().table("data_lineage").insert(rows).execute().data or rows
    except Exception:
        return rows
