"""RESEARCH LAYER: additive deep research after base enrichment."""
from __future__ import annotations
from app.integrations.prod.lead_research import deep_research_lead
from app.layers.persistence_layer import record_evidence

async def research(lead: dict, **kwargs) -> dict:
    result = await deep_research_lead(lead, **kwargs)
    await record_evidence(str(result.get("id") or lead.get("id") or ""), result.get("research_evidence") or [])
    return result
