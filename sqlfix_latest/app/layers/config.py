"""Layer/provider configuration registry.

Environment variables select implementations without coupling the orchestrator
to provider SDKs. Unknown values safely fall back to the production defaults.
"""
import os

DEFAULTS={
    "discovery":"multi_source",
    "enrichment":"cascade",
    "research":"parallel_evidence",
    "scoring":"deterministic_v3",
    "persistence":"supabase",
    "delivery":"policy_ranked",
}

def provider(layer:str)->str:
    value=os.getenv(f"LEADX_{layer.upper()}_PROVIDER",DEFAULTS.get(layer,"" )).strip().lower()
    return value or DEFAULTS.get(layer,"")
