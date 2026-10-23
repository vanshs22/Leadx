"""Evidence-grounded AI brief and next-best-action primitives."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from app.memory.supabase_client import get_supabase
from app.integrations.prod.opportunity import attach_opportunity_intelligence


def _sb(): return get_supabase()

def build_lead_brief(lead: dict[str, Any], offer: str = "") -> dict:
    enriched = attach_opportunity_intelligence(dict(lead), str(lead.get("industry") or lead.get("category") or ""))
    evidence = enriched.get("opportunity_evidence") or []
    actions = []
    if enriched.get("contactability_score", 0) >= 60: actions.append("Contact the best verified decision-maker")
    if enriched.get("opportunity_score", 0) >= 65: actions.append("Lead with the observed business opportunity")
    if lead.get("has_booking") is False: actions.append("Mention the booking/conversion gap if relevant")
    if lead.get("google_rating") and float(lead.get("google_rating") or 0) < 4: actions.append("Use reputation evidence carefully")
    if not actions: actions.append("Research the strongest current signal before outreach")
    return {"summary": f"{lead.get('name') or 'This business'} has a {enriched['opportunity_score_overall']}/100 opportunity signal based on observed evidence.", "why_now": evidence[:5], "next_best_actions": actions[:3], "scores": {k: enriched.get(k) for k in ('fit_score','opportunity_score','contactability_score','opportunity_score_overall')}, "confidence": enriched.get("intelligence_confidence", 0.5), "offer": offer}

def persist_brief(tenant_id: str | None, lead_id: str, brief: dict, model_version: str = "leadx-evidence-v1") -> dict:
    row = {"tenant_id": tenant_id, "lead_id": lead_id, "model_version": model_version, "summary": brief.get("summary"), "why_now": brief.get("why_now",[]), "next_best_actions": brief.get("next_best_actions",[]), "scores": brief.get("scores",{}), "confidence": brief.get("confidence"), "created_at": datetime.now(timezone.utc).isoformat()}
    return (_sb().table("lead_ai_briefs").insert(row).execute().data or [row])[0]


def validate_claims(text: str, evidence: list[dict]) -> dict:
    """Conservative hallucination firewall for outbound AI copy.
    Claims that cannot be mapped to supplied evidence are marked for review.
    This is intentionally deterministic; it never invents supporting evidence.
    """
    import re
    clean=(text or '').strip()
    snippets=' '.join(str((e or {}).get('text') or (e or {}).get('excerpt') or (e or {}).get('summary') or '') for e in evidence).lower()
    sentences=[s.strip() for s in re.split(r'(?<=[.!?])\s+',clean) if s.strip()]
    risky=[]
    for s in sentences:
        low=s.lower()
        if any(k in low for k in ('i noticed','i saw','your website','your reviews','you recently','you have','you use','you offer','you opened','you launched')):
            tokens=[t for t in re.findall(r'[a-z0-9]{4,}',low) if t not in {'noticed','website','reviews','recently','your','have','offer','opened','launched'}]
            overlap=sum(1 for t in tokens if t in snippets)
            if tokens and overlap/max(1,len(tokens)) < 0.12: risky.append({'sentence':s,'reason':'insufficient_evidence'})
    return {'ok':not risky,'review_required':bool(risky),'risky_claims':risky,'evidence_count':len(evidence)}


def record_ai_feedback(tenant_id:str, lead_id:str, signal:str, feedback:str, actor_id:str|None=None, metadata:dict|None=None):
    row={'tenant_id':tenant_id,'lead_id':lead_id,'signal':signal,'feedback':feedback,'actor_id':actor_id,'metadata':metadata or {}}
    try:return (_sb().table('intelligence_feedback').insert(row).execute().data or [row])[0]
    except Exception:return row
