"""SCORING/GATING LAYER: deterministic qualification only."""
from app.integrations.prod.quality import score_lead, score_lead_detailed, passes_quality_gate

def score(lead: dict, vertical: str):
    return score_lead(lead, vertical)

def score_detailed(lead: dict, vertical: str):
    return score_lead_detailed(lead, vertical)

def gate(lead: dict, *, allow_tier_b: bool = True, allow_tier_c: bool = False, require_contact: str = "phone_or_email"):
    return passes_quality_gate(lead, allow_tier_b=allow_tier_b, allow_tier_c=allow_tier_c, require_contact=require_contact)
