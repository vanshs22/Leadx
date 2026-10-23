"""LeadX Agent OS.

Each agent owns a bounded revenue task, persists its run/memory, and uses the
existing layered discovery/enrichment/research/scoring/CRM primitives instead
of creating a second data path. Agents are queue-first: API requests create a
 durable run and a jobs row; workers execute it and record the outcome.
"""
from __future__ import annotations

from app.services.aio import aexec, offload
import logging
from datetime import datetime, timezone
from typing import Any

from app.memory.supabase_client import get_supabase

log = logging.getLogger("lead_engine.agents")

from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY as AGENTS

CAPABILITIES = [
    ("ICP Builder", "prospecting"), ("Prospect Search", "prospecting"), ("Waterfall Enrichment", "enrichment"),
    ("Custom Research Fields", "research"), ("Evidence & Confidence", "research"), ("Opportunity Score", "qualification"),
    ("Why Now", "signal"), ("Buying Signals", "signal"), ("Trigger Plays", "signal"), ("Adaptive Sequences", "outreach"),
    ("AI Messaging", "outreach"), ("Account Memory", "research"), ("Deal Health", "deal"), ("Next Best Action", "crm"),
    ("Inbound Qualification", "qualification"), ("Website Intent", "signal"), ("Email Intelligence", "crm"),
    ("Automatic CRM Updates", "crm"), ("Revenue Graph", "revenue"), ("Revenue Analyst", "revenue"),
    ("Win/Loss Learning", "learning"), ("Loss Pattern Detection", "learning"), ("Intelligent Routing", "crm"),
    ("Data Health", "enrichment"), ("Provider Waterfall", "enrichment"), ("Integrations", "crm"),
    ("Autonomous Campaigns", "prospecting"), ("Prospecting Agent", "prospecting"), ("Research Agent", "research"),
    ("Deal Agent", "deal"),
]


def _sb(): return get_supabase()
def _now(): return datetime.now(timezone.utc).isoformat()


def list_agents() -> list[dict]:
    return [{"id": k, **v, "capabilities": [n for n, a in CAPABILITIES if a == k]} for k, v in AGENTS.items()]


def create_run(tenant_id: str, agent_id: str, task: str = "", input_data: dict | None = None, created_by: str = "user") -> dict:
    if agent_id not in AGENTS:
        raise ValueError("unknown_agent")
    row = {
        "tenant_id": tenant_id, "agent_id": agent_id, "task": task[:2000],
        "input_data": input_data or {}, "status": "queued", "created_by": created_by,
        "created_at": _now(), "updated_at": _now(),
    }
    out = _sb().table("agent_runs").insert(row).execute().data or []
    return out[0] if out else row


def update_run(run_id: str, **patch: Any) -> dict:
    patch["updated_at"] = _now()
    out = _sb().table("agent_runs").update(patch).eq("id", run_id).execute().data or []
    return out[0] if out else {}


def list_runs(tenant_id: str, limit: int = 50) -> list[dict]:
    return _sb().table("agent_runs").select("*").eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limit).execute().data or []


def record_memory(tenant_id: str, agent_id: str, key: str, value: Any, confidence: float = 0.5, source_run_id: str | None = None) -> None:
    payload = {"tenant_id": tenant_id, "agent_id": agent_id, "memory_key": key[:200], "memory_value": value if isinstance(value, dict) else {"value": value}, "confidence": max(0, min(1, float(confidence))), "source_run_id": source_run_id, "updated_at": _now()}
    try:
        _sb().table("agent_memory").upsert(payload, on_conflict="tenant_id,agent_id,memory_key").execute()
    except Exception:
        log.warning("agent memory write failed", exc_info=True)


def _lead(lead_id: str, tenant_id: str, *, mission_id: str | None = None) -> dict:
    # Direct agent runs may only touch leads already assigned to the tenant.
    # A GTM mission is different: discovery creates global leads first and CRM
    # assignment happens near the end of the mission. Upstream mission agents
    # therefore need read access to the global lead before assignment. The
    # mission_id is the explicit boundary; arbitrary direct runs keep the old
    # tenant-ownership requirement.
    if not mission_id:
        assignments = (_sb().table("lead_assignments").select("lead_id")
                       .eq("lead_id", lead_id).eq("tenant_id", tenant_id)
                       .not_.in_("status", ["rejected", "expired"]).limit(1).execute().data or [])
        if not assignments:
            raise ValueError("lead_not_found_or_not_owned")
    rows = _sb().table("leads_global").select("*").eq("id", lead_id).limit(1).execute().data or []
    if not rows:
        raise ValueError("lead_not_found")
    lead = dict(rows[0])
    # Hand-off: upstream agents (enrichment/research) persist into the `enrichment` table, so overlay it here;
    # otherwise verification/qualification never see the email, owner, services or booking data they produced.
    try:
        en = (_sb().table("enrichment").select("*").eq("lead_id", lead_id).limit(1).execute().data or [None])[0]
        for k, v in (en or {}).items():
            if k in ("lead_id", "id", "created_at", "updated_at", "error") or v in (None, "", [], {}):
                continue
            if lead.get(k) in (None, "", [], {}):
                lead[k] = v
    except Exception as exc:
        import logging; logging.getLogger(__name__).warning("enrichment overlay failed for %s: %s", lead_id, exc)
    return lead


async def execute_agent_task(run: dict, expected_agent: str | None = None) -> dict:
    tenant_id = str(run["tenant_id"]); agent = run["agent_id"]; data = run.get("input_data") or {}
    if expected_agent and agent != expected_agent: raise ValueError("agent_contract_mismatch")
    rid = str(run["id"])
    (await offload(update_run, rid, status="running", started_at=_now()))
    try:
        if agent == "prospecting":
            # In a GTM mission this stage owns ICP/target preparation. Direct
            # Prospecting runs retain the original full production pipeline.
            if data.get("mission_id"):
                result = {"agent":"prospecting","mission_stage":"icp_targeting",
                          "industry":str(data.get("industry") or "local business"),
                          "locations":list(data.get("locations") or []),
                          "queries":list(data.get("queries") or []),
                          "target_new_leads":int(data.get("target_new_leads") or 25),
                          "icp_id":data.get("icp_id"),
                          "objective":str(data.get("objective") or run.get("task") or "")}
            else:
                from app.integrations.prod.pipeline import run_pipeline
                result = await run_pipeline(
                    tenant_id=tenant_id, icp_id=data.get("icp_id"),
                    industry=str(data.get("industry") or "local business"), locations=data.get("locations"),
                    queries=data.get("queries"), allow_tier_b=True, deliver=bool(data.get("deliver", True)),
                    exhaustive_discovery=bool(data.get("exhaustive_discovery", True)),
                    target_new_leads=int(data.get("target_new_leads") or 25), max_assignments=int(data.get("max_assignments") or data.get("target_new_leads") or 25),
                    min_score=int(data.get("min_score") or 0),
                    execution_context=run.get("_job_context"),
                )
        elif agent in {"enrichment", "research", "signal", "qualification"}:
            ids = [str(x) for x in (data.get("lead_ids") or ([data.get("lead_id")] if data.get("lead_id") else []))]
            if not ids: raise ValueError("lead_id_or_lead_ids_required")
            results = []
            for lid in ids[:100]:
                lead = (await offload(_lead, lid, tenant_id, mission_id=data.get("mission_id")))
                if agent == "enrichment":
                    from app.integrations.prod.enricher import enrich_one
                    from app.layers.persistence_layer import upsert_enrichment
                    from app.integrations.prod.waterfall import ProviderStats, WaterfallEngine
                    from app.integrations.prod.waterfall_providers import build_providers
                    if lead.get("website"):
                        e = await enrich_one(lead["website"], company_name=lead.get("name"), city=lead.get("city"))
                        merged = {**lead, **{k:v for k,v in e.items() if v not in (None, "", [], {})}}
                        # Waterfall fills whatever the site scrape could not (owner, linkedin, guessed email)
                        # and records per-field provenance + confidence.
                        wf = await WaterfallEngine(
                            build_providers(), stats=ProviderStats(),
                            budget_per_lead=float(data.get("budget") or 4.0),
                            context=__import__("app.integrations.prod.provider_execution", fromlist=["ProviderExecutionContext"]).ProviderExecutionContext.from_lead(lead, **dict(run.get("_job_context") or {})),
                        ).enrich(merged, ["owner_name", "linkedin_url", "phone", "email"])
                        for k, v in wf["patch"].items():
                            if k not in ("field_confidence", "field_source"): merged[k] = v
                        await upsert_enrichment(lid, merged)
                        from app.layers.persistence_layer import record_evidence
                        await record_evidence(lid, [
                            {"field": f, "value": a["value"], "source": a["winner"], "confidence": a["confidence"],
                             "metadata": {"agreeing": a["agreeing"], "conflicting": a["conflicting"], "attempts": a["attempts"]}}
                            for f, a in wf["audit"].items() if a.get("value") is not None])
                        results.append({"lead_id": lid, "ok": True, "enriched": True, "waterfall_fill_rate": wf["fill_rate"], "credits": wf["credits_spent"]})
                    else: results.append({"lead_id": lid, "ok": False, "reason": "no_website"})
                elif agent == "research":
                    from app.integrations.prod.lead_research import deep_research_lead
                    from app.layers.persistence_layer import record_evidence
                    from app.layers.persistence_layer import upsert_enrichment
                    merged = await deep_research_lead(lead)
                    await upsert_enrichment(lid, merged)
                    await record_evidence(lid, merged.get("research_evidence") or [])
                    results.append({"lead_id": lid, "ok": True, "sources": merged.get("research_sources", [])})
                elif agent == "signal":
                    from app.integrations.prod.opportunity import attach_opportunity_intelligence
                    from app.integrations.prod.evidence_graph import record_opportunity, record_signal
                    enriched = attach_opportunity_intelligence(lead, str(data.get("industry") or lead.get("industry") or ""))
                    evidence = enriched.get("opportunity_evidence", [])
                    await offload(record_opportunity, lid, "why_now", {"fit": enriched.get("fit_score"), "need": enriched.get("opportunity_score"), "timing": enriched.get("intent_score"), "contactability": enriched.get("contactability_score"), "evidence": enriched.get("intelligence_confidence", 0) * 100, "overall": enriched.get("opportunity_score_overall")}, tenant_id=tenant_id, why_now=evidence, action="Review the highest-confidence signal before outreach", evidence=evidence, confidence=enriched.get("intelligence_confidence", 0.5))
                    for ev in evidence[:5]:
                        await offload(record_signal, lid, "opportunity_evidence", min(100, 50 + len(str(ev))/3), enriched.get("intelligence_confidence", 0.5), "leadx_opportunity", {"text": str(ev)}, tenant_id=tenant_id)
                    results.append({"lead_id": lid, "ok": True, "why_now": evidence, "score": enriched.get("opportunity_score_overall")})
                else:
                    if agent == "qualification":
                        # Qualification is the actual decision gate, not merely
                        # a copywriting step. Reuse the canonical intelligence
                        # engine and persist the deterministic score used by
                        # the downstream CRM assignment gate.
                        from app.integrations.prod.lead_intelligence_6 import evaluate_lead
                        evaluation = evaluate_lead(lead, data.get("icp") or {"industries": [data.get("industry") or lead.get("industry")]})
                        scores = evaluation.get("scores") or {}
                        priority = int(round(float(scores.get("overall") or 0)))  # scores["priority"] is the P1-P4 label, not a number
                        tier = "A" if priority >= 80 else "B" if priority >= 60 else "C"
                        await offload(lambda: _sb().table("scores").upsert({
                            "lead_id": lid,
                            "deterministic_score": priority,
                            "total_score": priority,
                            "tier": tier,
                            "reasoning": "; ".join(evaluation.get("explainability", {}).get("why_this_lead") or [])[:2000],
                        }, on_conflict="lead_id").execute())  # .execute() was missing: the score was never saved
                        results.append({"lead_id": lid, "ok": True, "qualification": evaluation, "qualified": bool(evaluation.get("gate", {}).get("eligible")), "score": priority, "tier": tier})
                    else:
                        from app.integrations.prod.ai_intelligence import build_lead_brief, persist_brief
                        brief = build_lead_brief(lead, str(data.get("offer") or "")); persist_brief(tenant_id, lid, brief)
                        results.append({"lead_id": lid, "ok": True, "brief": brief})
            # Hand-off contract: the mission graph feeds `lead_ids` of this result to the next agent.
            # Qualification only forwards leads that passed the gate; the others stop here.
            _ok = [r for r in results if r.get("ok")]
            if agent == "qualification":
                _ok = [r for r in _ok if r.get("qualified")]
            result = {"processed": len(results), "results": results, "lead_ids": [str(r["lead_id"]) for r in _ok]}
        elif agent == "outreach":
            from app.integrations.prod.ai_intelligence import build_lead_brief
            ids = [str(x) for x in (data.get("lead_ids") or ([data.get("lead_id")] if data.get("lead_id") else []))]
            briefs = [{"lead_id": lid, "brief": build_lead_brief((await offload(_lead, lid, tenant_id, mission_id=data.get("mission_id"))), str(data.get("offer") or ""))} for lid in ids[:50]]
            enrolled = 0
            from app.integrations.prod.approval_inbox import auto_approve_enabled
            tenant_auto_approve = auto_approve_enabled(tenant_id)
            if data.get("sequence_id") and data.get("assignment_ids") and tenant_auto_approve:
                from app.integrations.prod.sequences import enroll_leads
                enr = await enroll_leads(tenant_id, str(data["sequence_id"]), [str(x) for x in data["assignment_ids"]], ai_copy_enabled=bool(data.get("ai_copy", True)))
                enrolled = int(enr.get("enrolled") or 0)
            from app.integrations.prod.approval_inbox import create_outreach_draft
            approval_items=[]
            assignment_ids=[str(x) for x in (data.get("assignment_ids") or [])]
            for item in briefs:
                aid=assignment_ids[0] if len(assignment_ids)==1 else None
                draft={**(item.get("brief") or {}),"sequence_id":data.get("sequence_id"),"ai_copy":bool(data.get("ai_copy",True))}
                approval_items.append(create_outreach_draft(tenant_id,rid,item.get("lead_id"),draft,assignment_id=aid))
            result = {"actions": briefs, "mode": "auto_approved" if tenant_auto_approve else "needs_approval", "approval_items": approval_items, "sequence_enrolled": enrolled if enrolled and tenant_auto_approve else 0}
        elif agent == "crm":
            if data.get("mission_id") and data.get("lead_ids"):
                from app.integrations.prod.quality import assign_lead
                from app.services.delivery import deliver_batch
                ids=[str(x) for x in data.get("lead_ids")][:5000]
                rows=(await offload(_sb().table("leads_global").select("*").in_("id", ids).execute)).data or []
                score_rows=(await offload(_sb().table("scores").select("lead_id,total_score,deterministic_score,tier").in_("lead_id",ids))).data or []
                score_map={str(x.get("lead_id")):x for x in score_rows}
                assignment_ids=[]; assigned=[]; blockers=[]
                for lead in rows:
                    sc=score_map.get(str(lead["id"])) or {}
                    score=int(lead.get("score") or sc.get("total_score") or sc.get("deterministic_score") or 0)
                    tier=str(lead.get("tier") or sc.get("tier") or ("A" if score >= 70 else "B" if score >= 50 else "C"))
                    if tier == "C" and not bool(data.get("allow_tier_c",False)): continue
                    ar=await assign_lead(str(lead["id"]),tenant_id,data.get("icp_id"),str(data.get("industry") or lead.get("category") or "local business"),list(data.get("locations") or []),delivery_channel=str(data.get("delivery_channel") or "portal"),tier=tier,offer_category=data.get("offer_category"),discovery_request_id=data.get("discovery_request_id"))
                    if ar.get("assigned"):
                        assignment_ids.append(str(ar.get("assignment_id"))); assigned.append(lead)
                    else: blockers.append({"lead_id":str(lead["id"]),"reason":ar.get("reason")})
                delivery=await deliver_batch(assigned,str(data.get("delivery_channel") or "portal"),data.get("delivery_config") or {},assignment_ids)
                result={"agent":"crm","mission_stage":"assignment_delivery","assigned":len(assigned),"assignment_ids":assignment_ids,"lead_ids":[str(x["id"]) for x in assigned],"blockers":blockers,"delivery":delivery}
            else:
                from app.integrations.prod.revenue_ops import daily_brief
                result = {"daily_brief": await offload(daily_brief, tenant_id, int(data.get("days") or 1))}
        elif agent == "deal":
            from app.integrations.prod.revenue_ops import daily_brief
            from app.integrations.prod.revenue_intelligence import tenant_funnel
            funnel, brief = await __import__("asyncio").gather(
                offload(tenant_funnel, tenant_id, int(data.get("days") or 90)),
                offload(daily_brief, tenant_id, int(data.get("days") or 7)),
            )
            result = {"funnel": funnel, "brief": brief}
        elif agent == "revenue":
            from app.integrations.prod.revenue_ops import source_roi, daily_brief
            sources, brief = await __import__("asyncio").gather(
                offload(source_roi, tenant_id, int(data.get("days") or 90)),
                offload(daily_brief, tenant_id, int(data.get("days") or 30)),
            )
            result = {"sources": sources, "brief": brief}
        elif agent == "learning":
            from app.integrations.prod.learning_loop import learning_snapshot
            result = await learning_snapshot(tenant_id)
            (await offload(record_memory, tenant_id, agent, "latest_learning_snapshot", result, 0.8, rid))
        else:
            raise ValueError("agent_not_implemented")
        verification = {"checked": True, "summary": _summary(result), "verified_at": _now()}
        (await offload(update_run, rid, status="done", result=result, verification=verification, outcome=_summary(result), finished_at=_now()))
        (await offload(record_memory, tenant_id, agent, "last_success", {"run_id": rid, "result_summary": _summary(result)}, 0.8, rid))
        # Persist account-level memory when a run touched leads. This is additive
        # and tenant-scoped; it never replaces the canonical CRM record.
        lead_ids = []
        if isinstance(result, dict):
            for item in (result.get("results") or result.get("actions") or []):
                if isinstance(item, dict) and item.get("lead_id"):
                    lead_ids.append(str(item["lead_id"]))
        if lead_ids:
            for lid in list(dict.fromkeys(lead_ids))[:100]:
                try:
                    await offload(lambda lid=lid: _sb().table("lead_account_memory").upsert({
                        "tenant_id": tenant_id, "lead_id": lid, "memory_key": "last_agent_touch",
                        "memory_value": {"agent_id": agent, "run_id": rid, "summary": _summary(result)},
                        "confidence": 0.8, "source_type": "agent_run", "source_ref": rid,
                        "observed_at": _now(), "updated_at": _now(),
                    }, on_conflict="tenant_id,lead_id,memory_key").execute())  # .execute() was missing: memory never saved
                except Exception:
                    log.warning("account memory write failed for lead %s", lid, exc_info=True)
        goal_id = run.get("goal_id")
        if goal_id:
            try:
                goal_rows = await offload(_sb().table("agent_goals").select("target,current_state").eq("id", str(goal_id)).limit(1).execute)
                goal_rows = getattr(goal_rows, "data", None) or []
                if goal_rows:
                    current = goal_rows[0].get("current_state") or {}
                    if agent == "prospecting":
                        current["qualified_count"] = int(current.get("qualified_count") or 0) + int(result.get("assigned_to_tenant") or result.get("delivered") or 0)
                    current["last_result"] = _summary(result)
                    await offload(_sb().table("agent_goals").update({"current_state": current, "last_run_id": rid, "last_error": None}).eq("id", str(goal_id)).execute)
            except Exception:
                log.warning("goal state update failed for %s", goal_id, exc_info=True)
        return {"ok": True, "run_id": rid, "result": result}
    except Exception as exc:
        (await offload(update_run, rid, status="failed", error=str(exc)[:2000], finished_at=_now()))
        if run.get("goal_id"):
            try:
                await offload(_sb().table("agent_goals").update({"last_error": str(exc)[:1000], "last_run_id": rid}).eq("id", str(run["goal_id"])).execute)
            except Exception:
                log.warning("goal error update failed", exc_info=True)
        raise


def _summary(result: Any) -> dict:
    if isinstance(result, dict):
        return {k: result[k] for k in list(result)[:12] if k not in {"results", "actions"}}
    return {"value": str(result)[:500]}


def feedback(tenant_id: str, agent_id: str, run_id: str | None, feedback_text: str, metadata: dict | None = None) -> dict:
    row = {"tenant_id": tenant_id, "agent_id": agent_id, "run_id": run_id, "feedback": feedback_text[:4000], "metadata": metadata or {}, "created_at": _now()}
    out = _sb().table("agent_feedback").insert(row).execute().data or []
    record_memory(tenant_id, agent_id, "human_feedback", {"feedback": feedback_text[:1000], "metadata": metadata or {}}, 0.9, run_id)
    return out[0] if out else row
