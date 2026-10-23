"""Static production-readiness audit for LeadX 15.8.
Does not pretend that external provider credentials or a live Supabase instance exist.
"""
from __future__ import annotations
import ast, json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from app.integrations.prod.discovery_catalog import discovery_source_count, discovery_lane_catalog
    from app.integrations.prod.enrichment_mesh import PAID_PROVIDER_CATALOG
    from app.integrations.prod.agent_registry import FULL_AGENT_REGISTRY
    checks={
      "discovery_sources": discovery_source_count(),
      "discovery_lanes": len(discovery_lane_catalog()),
      "enrichment_provider_catalog": len(PAID_PROVIDER_CATALOG),
      "agents": len(FULL_AGENT_REGISTRY),
      "mission_migration": (ROOT/'supabase/migrations/20261001000200_leadx_15_agent_missions.sql').exists(),
      "mission_api": (ROOT/'app/api/prod/agent_missions_api.py').exists(),
      "realtime_client": (ROOT/'lead-crm-portal/lib/crmRealtime.ts').exists(),
      "crm_api": (ROOT/'app/crm/crm_api.py').exists(),
    }
    assert checks["discovery_sources"] >= 1200 and checks["discovery_lanes"] >= 1200
    assert checks["enrichment_provider_catalog"] >= 80
    assert checks["agents"] >= 30
    assert all(checks[k] for k in ("mission_migration","mission_api","realtime_client","crm_api"))
    print(json.dumps({"ok":True,"version":"15.8.1","checks":checks},indent=2))

if __name__=='__main__': main()
