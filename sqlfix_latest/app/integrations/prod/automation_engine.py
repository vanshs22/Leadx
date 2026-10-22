"""LeadX 9.0 event-driven automation engine.

Durable events are queued in Postgres and processed asynchronously by workers.
Workflow graphs are intentionally bounded: conditions + explicit actions only;
no arbitrary code execution and no unbounded recursion.
"""
from __future__ import annotations
import os, socket
from datetime import datetime, timezone, timedelta
from typing import Any
from app.services.aio import aexec, offload
from app.memory.supabase_client import get_supabase

WORKER = f"automation:{socket.gethostname()}:{os.getpid()}"
ALLOWED_ACTIONS = {"send_email","enroll_sequence","stop_sequence","create_task","notify_owner","create_deal","advance_deal_stage","close_deal","update_lead","escalate_owner","score_reply"}

def _sb(): return get_supabase()

def enqueue_event(tenant_id: str, event_type: str, subject_type: str, subject_id: str|None, payload: dict, key: str) -> str|None:
    try:
        r = _sb().rpc("enqueue_automation_event", {"p_tenant_id":tenant_id,"p_event_type":event_type,"p_subject_type":subject_type,"p_subject_id":subject_id,"p_payload":payload or {},"p_idempotency_key":key}).execute()
        return str(r.data) if r.data else None
    except Exception:
        return None

def _match(trigger: dict, event: dict) -> bool:
    if trigger.get("event") and trigger.get("event") != event.get("event_type"): return False
    conditions = trigger.get("conditions") or []
    payload = event.get("payload") or {}
    for c in conditions:
        field, op, expected = c.get("field"), c.get("op","eq"), c.get("value")
        actual = payload.get(field)
        if op == "eq" and actual != expected: return False
        if op == "neq" and actual == expected: return False
        if op == "gte" and float(actual or 0) < float(expected): return False
        if op == "lte" and float(actual or 0) > float(expected): return False
        if op == "contains" and str(expected).lower() not in str(actual or "").lower(): return False
    return True

async def _action(tenant_id: str, action: dict, event: dict) -> dict:
    kind = action.get("type")
    if kind not in ALLOWED_ACTIONS: return {"ok":False,"error":"unsupported_action"}
    p = {**(event.get("payload") or {}), **(action.get("params") or {})}
    sb = _sb()
    if kind == "score_reply": return {"ok":True,"score":p.get("reply_score",0)}
    if kind == "notify_owner":
        from app.integrations.prod.unsubscribe import notify_tenant
        await notify_tenant(tenant_id, str(p.get("message") or "LeadX automation notification"))
        return {"ok":True}
    if kind == "create_task":
        row = {"tenant_id":tenant_id,"title":str(p.get("title") or "Follow up with lead"),"description":str(p.get("description") or "Created by LeadX automation"),"priority":str(p.get("priority") or "high"),"status":"open","assigned_to":p.get("owner")}
        if p.get("deal_id"): row["deal_id"]=p["deal_id"]
        if p.get("deal_id"):
            existing = (await aexec(sb.table("tasks").select("id").eq("tenant_id", tenant_id).eq("deal_id", p["deal_id"]).eq("title", row["title"]).eq("status", "open").limit(1))).data or []
            if existing:
                return {"ok": True, "task_id": existing[0]["id"], "existing": True}
        out=(await aexec(sb.table("tasks").insert(row))).data or []
        return {"ok":True,"task_id":out[0].get("id") if out else None}
    if kind == "create_deal":
        aid=p.get("assignment_id")
        if not aid: return {"ok":False,"error":"assignment_required"}
        existing=(await aexec(sb.table("deals").select("id").eq("tenant_id",tenant_id).eq("source_assignment_id",aid).limit(1))).data or []
        if existing: return {"ok":True,"deal_id":existing[0]["id"],"existing":True}
        a=(await aexec(sb.table("lead_assignments").select("lead_id,assigned_to,value_estimate").eq("id",aid).eq("tenant_id",tenant_id).limit(1))).data or []
        if not a: return {"ok":False,"error":"assignment_not_found"}
        lead=(await aexec(sb.table("leads_global").select("name").eq("id",a[0]["lead_id"]).limit(1))).data or []
        title=f"{(lead[0].get('name') if lead else 'New lead')} opportunity"
        stages=(await aexec(sb.table("deal_stages").select("id").eq("tenant_id",tenant_id).eq("is_won",False).eq("is_lost",False).order("position").limit(1))).data or []
        row={"tenant_id":tenant_id,"title":title[:200],"status":"open","source":"leadx_automation","source_assignment_id":aid,"owner":a[0].get("assigned_to"),"value":a[0].get("value_estimate")}
        if stages: row["stage_id"]=stages[0]["id"]
        out=(await aexec(sb.table("deals").insert(row))).data or []
        return {"ok":True,"deal_id":out[0].get("id") if out else None}
    if kind in {"advance_deal_stage", "close_deal"}:
        deal_id = p.get("deal_id")
        if not deal_id and p.get("assignment_id"):
            created = await _action(tenant_id, {"type": "create_deal", "params": p}, event)
            deal_id = created.get("deal_id")
        if not deal_id:
            return {"ok": False, "error": "deal_or_assignment_required"}
        stage_query = sb.table("deal_stages").select("id,slug,name,probability,is_won,is_lost").eq("tenant_id", tenant_id)
        if kind == "close_deal":
            outcome = str(p.get("outcome") or "won").lower()
            stage_query = stage_query.eq("is_won", outcome == "won").eq("is_lost", outcome == "lost")
        elif p.get("stage_slug"):
            stage_query = stage_query.eq("slug", str(p["stage_slug"]))
        stages = (await aexec(stage_query.order("position").limit(1))).data or []
        if not stages:
            return {"ok": False, "error": "deal_stage_not_found"}
        stage = stages[0]
        patch = {"stage_id": stage["id"], "probability": stage.get("probability"), "updated_at": datetime.now(timezone.utc).isoformat()}
        if kind == "close_deal":
            patch.update({"status": "won" if stage.get("is_won") else "lost", "actual_close_date": datetime.now(timezone.utc).date().isoformat()})
        updated = (await aexec(sb.table("deals").update(patch).eq("id", deal_id).eq("tenant_id", tenant_id))).data or []
        return {"ok": bool(updated), "deal_id": deal_id, "stage_id": stage["id"], "status": patch.get("status", "open")}
    if kind == "escalate_owner":
        deal = await _action(tenant_id,{"type":"create_deal","params":p},event)
        task = await _action(tenant_id,{"type":"create_task","params":{**p,"deal_id":deal.get("deal_id"),"priority":"urgent","title":p.get("title") or "Hot reply — owner action required"}},event)
        return {"ok":True,"deal":deal,"task":task}
    if kind == "enroll_sequence":
        from app.integrations.prod.sequences import enroll_leads
        return await enroll_leads(tenant_id,str(p.get("sequence_id")),[str(p["assignment_id"])],ai_copy_enabled=bool(p.get("ai_copy_enabled",False)))
    if kind == "stop_sequence":
        q=sb.table("email_sequence_enrollments").update({"status":"stopped_automation","locked_at":None,"locked_by":None}).eq("tenant_id",tenant_id).eq("assignment_id",p.get("assignment_id")).in_("status",["active","sending"])
        return {"ok":bool((await aexec(q)).data is not None)}
    if kind == "update_lead":
        allowed={"lead_state","priority_score","lead_value_score","risk_score","gate_decision"}
        patch={k:v for k,v in (action.get("params") or {}).items() if k in allowed}
        if not patch or not p.get("lead_id"): return {"ok":False,"error":"safe_lead_patch_required"}
        # leads_global is intentionally shared across tenants. Prove tenant ownership
        # through lead_assignments before mutating the global entity.
        owned = (await aexec(
            sb.table("lead_assignments").select("id,lead_id").eq("tenant_id",tenant_id).eq("lead_id",p["lead_id"]).limit(1)
        )).data or []
        if not owned:
            return {"ok":False,"error":"lead_not_assigned_to_tenant"}
        out=(await aexec(sb.table("leads_global").update(patch).eq("id",p["lead_id"]).limit(1))).data or []
        return {"ok":bool(out),"error":None if out else "lead_not_found"}
    if kind == "send_email":
        from app.integrations.prod.email_outreach import send_to_lead
        if not p.get("assignment_id"): return {"ok":False,"error":"assignment_required"}
        active = (await aexec(sb.table("email_sequence_enrollments").select("id").eq("tenant_id", tenant_id).eq("assignment_id", p["assignment_id"]).in_("status", ["active", "sending"]).limit(1))).data or []
        if active:
            return {"ok":False,"error":"assignment_owned_by_sequence"}
        return await send_to_lead(tenant_id,p["assignment_id"],str(p.get("subject") or ""),str(p.get("body_html") or ""),str(p.get("body_text") or ""),reply_to=p.get("reply_to"),provider_operation_id=f"leadx:automation:{event.get('id') or p.get('event_id') or p.get('assignment_id')}")
    return {"ok":False,"error":"unhandled_action"}

async def process_events(limit: int = 100) -> dict:
    limit=max(1,min(int(limit or 100),500))
    rows=(await aexec(_sb().table("automation_events").select("*").eq("status","queued").lte("available_at",datetime.now(timezone.utc).isoformat()).order("created_at").limit(limit))).data or []
    processed=0; matched=0; failed=0
    for event in rows:
        claimed=(await aexec(_sb().table("automation_events").update({"status":"processing","locked_at":datetime.now(timezone.utc).isoformat(),"locked_by":WORKER,"attempts":int(event.get("attempts") or 0)+1}).eq("id",event["id"]).eq("status","queued"))).data or []
        if not claimed: continue
        try:
            workflows=(await aexec(_sb().table("workflow_definitions").select("*").eq("tenant_id",event["tenant_id"]).eq("enabled",True).eq("trigger_type",event["event_type"]).limit(100))).data or []
            event_matched = False
            for wf in workflows:
                graph=wf.get("graph") or {}
                trigger=graph.get("trigger") or {"event":event["event_type"],"conditions":graph.get("conditions") or []}
                if not _match(trigger,event): continue
                matched+=1
                event_matched = True
                idem=f"{wf['id']}:{event['id']}"
                # Idempotency is enforced before executing actions. An upsert alone
                # can return an already-completed row and accidentally replay every
                # action on a retry/reconnect.
                existing=(await aexec(_sb().table("workflow_runs").select("id,status").eq("workflow_id",wf["id"]).eq("idempotency_key",idem).limit(1))).data or []
                if existing and existing[0].get("status") == "completed":
                    continue
                if existing and existing[0].get("status") in {"running","processing"}:
                    continue
                if existing:
                    run_id=existing[0]["id"]
                    await aexec(_sb().table("workflow_runs").update({"status":"running","event_id":event["id"],"context":event.get("payload") or {},"current_node":"start","error":None}).eq("id",run_id))
                else:
                    run=(await aexec(_sb().table("workflow_runs").insert({"workflow_id":wf["id"],"tenant_id":event["tenant_id"],"event_id":event["id"],"idempotency_key":idem,"status":"running","context":event.get("payload") or {},"current_node":"start"}))).data or []
                    if not run: continue
                    run_id=run[0]["id"]
                # Per-workflow concurrency/cooldown guard. These are soft guards;
                # the durable idempotency key remains the hard duplicate boundary.
                max_concurrency=max(1,min(int(wf.get("max_concurrency") or 10),100))
                active=(await aexec(_sb().table("workflow_runs").select("id",count="exact").eq("workflow_id",wf["id"]).in_("status",["running","processing"]))).count or 0
                if active > max_concurrency:
                    await aexec(_sb().table("workflow_runs").update({"status":"queued","current_node":"waiting_for_capacity"}).eq("id",run_id))
                    continue
                cooldown=max(0,int(wf.get("cooldown_seconds") or 0))
                if cooldown:
                    cutoff=(datetime.now(timezone.utc)-timedelta(seconds=cooldown)).isoformat()
                    recent=(await aexec(_sb().table("workflow_runs").select("id").eq("workflow_id",wf["id"]).gte("created_at",cutoff).neq("id",run_id).limit(1))).data or []
                    if recent:
                        await aexec(_sb().table("workflow_runs").update({"status":"queued","current_node":"cooldown"}).eq("id",run_id))
                        continue
                for action in (graph.get("actions") or [])[:25]:
                    res=await _action(event["tenant_id"],action,event)
                    if not res.get("ok") and action.get("required",True): raise RuntimeError(res.get("error") or "automation_action_failed")
                await aexec(_sb().table("workflow_runs").update({"status":"completed","current_node":"done","completed_at":datetime.now(timezone.utc).isoformat()}).eq("id",run_id))
            # Unified growth fallback: a positive reply always becomes an
            # opportunity and owner task even when the tenant has not created a
            # custom workflow. Custom workflows remain the extension point.
            payload = event.get("payload") or {}
            if (event.get("event_type") == "email.reply_received"
                    and payload.get("assignment_id")
                    and (payload.get("category") == "positive" or int(payload.get("reply_score") or 0) >= 70)):
                deal = await _action(event["tenant_id"], {"type": "create_deal", "params": payload}, event)
                if deal.get("ok"):
                    await _action(event["tenant_id"], {"type": "advance_deal_stage", "params": {**payload, **deal, "stage_slug": "qualified"}}, event)
                    await _action(event["tenant_id"], {"type": "create_task", "params": {**payload, **deal, "priority": "high", "title": "Follow up on positive reply"}}, event)
                    matched += 1
            await aexec(_sb().table("automation_events").update({"status":"done","processed_at":datetime.now(timezone.utc).isoformat(),"locked_at":None,"locked_by":None}).eq("id",event["id"]))
            processed+=1
        except Exception as exc:
            failed+=1
            attempts=int(event.get("attempts") or 0)+1
            status="failed" if attempts>=5 else "queued"
            await aexec(_sb().table("automation_events").update({"status":status,"error":str(exc)[:1000],"available_at":(datetime.now(timezone.utc)+timedelta(minutes=min(60,2**attempts))).isoformat(),"locked_at":None,"locked_by":None}).eq("id",event["id"]))
    return {"ok":True,"processed":processed,"matched":matched,"failed":failed,"scanned":len(rows)}
