"""SCORING/GATING LAYER: deterministic qualification only."""
from app.integrations.prod.quality import score_lead, passes_quality_gate

def score(lead: dict, vertical: str):
    return score_lead(lead, vertical)

def gate(lead: dict, *, allow_tier_b: bool = True):
    return passes_quality_gate(lead, allow_tier_b=allow_tier_b)
