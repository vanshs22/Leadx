"""Compliance and data-governance primitives."""
from __future__ import annotations
from datetime import datetime, timezone
from app.memory.supabase_client import get_supabase

def _sb(): return get_supabase()
def compliance_event(event_type, *, tenant_id=None, subject_type='unknown', subject_id='', legal_basis=None, region=None, metadata=None):
    row={"tenant_id":tenant_id,"subject_type":subject_type,"subject_id":str(subject_id),"event_type":event_type,"legal_basis":legal_basis,"region":region,"metadata":metadata or {}}
    try:return (_sb().table('compliance_events').insert(row).execute().data or [row])[0]
    except Exception:return row

def create_dsr(tenant_id, subject_type, subject_value, request_type):
    row={"tenant_id":tenant_id,"subject_type":subject_type,"subject_value":subject_value.strip(),"request_type":request_type}
    try:return (_sb().table('data_subject_requests').insert(row).execute().data or [row])[0]
    except Exception:return row

def log_export(tenant_id, actor_id, export_type, row_count, fields, filters=None):
    row={"tenant_id":tenant_id,"actor_id":actor_id,"export_type":export_type,"row_count":row_count,"fields":fields,"filters":filters or {}}
    try:_sb().table('export_audit').insert(row).execute()
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return row
