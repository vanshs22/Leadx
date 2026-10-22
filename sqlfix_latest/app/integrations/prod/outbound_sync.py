from __future__ import annotations
from app.services.aio import aexec, offload
import hashlib,hmac,json,logging,time
import httpx
from app.memory.supabase_client import get_supabase
from app.integrations.prod.net_safety import assert_public_url

logger=logging.getLogger("lead_engine.outbound_sync")

def _sb(): return get_supabase()

def _delivery_lookup(tenant_id, assignment_id, provider):
    try:
        rows=_sb().table("integration_deliveries").select("id,status").eq("tenant_id",tenant_id).eq("assignment_id",assignment_id).eq("provider",provider).eq("status","delivered").limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None

def _delivery(row):
    try:
        return (_sb().table("integration_deliveries").insert(row).execute().data or [row])[0]
    except Exception as exc:
        logger.warning("integration delivery log failed: %s",exc)
        return row

def _update_delivery(delivery_id, patch):
    try: _sb().table("integration_deliveries").update(patch).eq("id",delivery_id).execute()
    except Exception as exc: logger.warning("integration delivery update failed: %s",exc)

def _link_after_push(tenant_id, assignment_id, lead_id, provider, account_id, response):
    """Return path for two-way sync: remember which external record this assignment became."""
    try:
        body=response.json() if response.content else {}
        ext=(body.get("id") if provider=="hubspot" else (body.get("contact") or {}).get("id") or body.get("id"))
        if not (ext and account_id): return
        from app.integrations.prod.crm_sync import link_external
        link_external(_sb(),tenant_id,provider,str(account_id),str(ext),assignment_id,lead_id)
    except Exception as exc:
        logger.warning("sync link after push failed: %s",exc)

async def push_assignment(tenant_id:str, assignment_id:str)->dict:
    rows=(await aexec(_sb().table("lead_assignments").select("id,lead_id,tier,stage_id").eq("id",assignment_id).eq("tenant_id",tenant_id).limit(1))).data or []
    if not rows: return {"ok":False,"error":"assignment_not_found"}
    a=rows[0]; leads=(await aexec(_sb().table("leads_global").select("*").eq("id",a["lead_id"]).limit(1))).data or []
    lead=leads[0] if leads else {}
    maps=(await aexec(_sb().table("integration_field_maps").select("provider,mapping,stage_mapping,auto_push").eq("tenant_id",tenant_id).eq("auto_push",True))).data or []
    results=[]
    for m in maps:
        p=m["provider"]
        if (await offload(_delivery_lookup, tenant_id,assignment_id,p)):
            results.append({"provider":p,"ok":True,"status":"already_delivered"}); continue
        from app.integrations.prod.connectors import _config, select_connection
        selected = await offload(select_connection, tenant_id, p)
        if not selected: continue
        cfgrows=[selected]
        try:
            prior=(await aexec(_sb().table("integration_deliveries").select("attempt").eq("tenant_id",tenant_id).eq("assignment_id",assignment_id).eq("provider",p).order("attempt",desc=True).limit(1))).data or []
            attempt=int(prior[0].get("attempt") or 0)+1 if prior else 1
        except Exception:
            attempt=1
        delivery=(await offload(_delivery, {"tenant_id":tenant_id,"assignment_id":assignment_id,"provider":p,"status":"pending","attempt":attempt,"payload":{"lead_id":a["lead_id"]}}))
        did=delivery.get("id")
        try:
            from app.integrations.prod.crm_delivery import execute_crm_delivery, make_request
            if p in {"salesforce", "hubspot", "pipedrive", "close", "gohighlevel"}:
                stable_event_id = f"assignment:{assignment_id}:delivery:{p}"
                request = make_request(
                    tenant_id=tenant_id, assignment_id=assignment_id, lead_id=str(a["lead_id"]),
                    provider=p, stable_event_id=stable_event_id, lead=lead, assignment=a,
                    mapping=m.get("mapping") or {}, payload_hash=None,
                )
                delivery_result = await execute_crm_delivery(request, cfg)
                result_data = delivery_result.as_dict()
                ok = bool(delivery_result.ok)
                if ok:
                    class _Response:
                        status_code = delivery_result.status_code or 200
                        content = b"1" if delivery_result.external_id else b""
                        def json(self): return {"id": delivery_result.external_id} if delivery_result.external_id else {}
                    if p in ("hubspot", "gohighlevel"):
                        _link_after_push(tenant_id, assignment_id, a["lead_id"], p, cfgrows[0].get("external_account_id") or cfg.get("location_id") or cfg.get("portal_id"), _Response())
                await offload(_update_delivery, did, {
                    "status": "delivered" if ok else "failed",
                    "http_code": delivery_result.status_code,
                    "error": None if ok else delivery_result.safe_error_detail,
                })
                try:
                    from app.integrations.prod.data_plane import record_delivery_outcome
                    await offload(
                        record_delivery_outcome,
                        tenant_id=tenant_id, assignment_ids=[assignment_id], destination=p,
                        ok=ok, error=delivery_result.safe_error_detail,
                        provider_operation_id=delivery_result.provider_operation_id,
                    )
                except Exception:
                    logger.debug("CRM outbox outcome projection skipped", exc_info=True)
                result_data["status"] = delivery_result.status_code or ("delivered" if ok else "failed")
                results.append(result_data)
                if not ok:
                    raise RuntimeError(delivery_result.safe_error_detail or delivery_result.safe_error_code or f"{p} delivery failed")
                continue
            async with httpx.AsyncClient(timeout=15,follow_redirects=False) as c:
                if p in ("webhook", "generic_http", "zapier", "make", "n8n"):
                    url=str(cfg.get("url") or "").strip(); secret=str(cfg.get("secret") or "").strip()
                    if not url: raise ValueError("webhook URL is required")
                    if not secret: raise ValueError("webhook secret is required")
                    reason=await assert_public_url(url)
                    if reason: raise ValueError(f"unsafe webhook URL: {reason}")
                    payload={"event":"lead.assigned","timestamp":int(time.time()),"assignment":a,"lead":lead}
                    body=json.dumps(payload,separators=(",",":"),sort_keys=True)
                    sig=hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest() if secret else ""
                    headers={"content-type":"application/json","x-leadx-timestamp":str(payload["timestamp"])}
                    if sig: headers["x-leadx-signature"]=sig
                    r=await c.post(url,content=body,headers=headers)
                else:
                    continue
            ok=r.status_code<400
            await offload(_update_delivery, did,{"status":"delivered" if ok else "failed","http_code":r.status_code,"error":None if ok else r.text[:500]})
            results.append({"provider":p,"ok":ok,"status":r.status_code})
            if not ok: raise RuntimeError(f"{p} delivery returned HTTP {r.status_code}")
        except Exception as e:
            await offload(_update_delivery, did,{"status":"failed","error":str(e)[:500]})
            logger.warning("integration push failed tenant=%s assignment=%s provider=%s: %s",tenant_id,assignment_id,p,e)
            results.append({"provider":p,"ok":False,"error":str(e)[:300]})
    failed=[x for x in results if not x.get("ok")]
    if failed: raise RuntimeError("integration delivery failed: "+json.dumps(failed)[:1000])
    return {"ok":True,"results":results}
