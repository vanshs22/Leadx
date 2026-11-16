"""Focused offline tests for the reconciled orchestration catalog."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.orchestration.catalog import (
    ALIAS_MAP,
    CATALOG,
    CATALOG_IDS,
    OLD_TO_NEW_MAPPING,
    SMART_PLANNER_ENTRY,
    CatalogValidationError,
    validate_catalog,
)
from app.orchestration.tools import TOOL_NAMES


EXPECTED_IDS = (
    "gtm", "prospecting", "enrichment", "research", "signal", "qualification",
    "outreach", "crm", "deal", "revenue", "learning", "discovery_planner",
    "source_router", "identity_resolution", "contact_finder", "email_finder",
    "verification", "web_research", "social_intelligence", "intent", "trigger",
    "icp", "personalization", "deliverability", "compliance", "routing",
    "crm_hygiene", "campaign_optimizer", "data_quality", "cost_optimizer", "experiment",
)

EXPECTED_ALIASES = {
    "GTM Mission Manager": "gtm",
    "ICP Builder": "icp",
    "Prospect Search": "prospecting",
    "Prospecting Agent": "prospecting",
    "Waterfall Enrichment": "enrichment",
    "Data Health": "data_quality",
    "Provider Waterfall": "cost_optimizer",
    "Custom Research Fields": "research",
    "Evidence & Confidence": "evidence",
    "Account Memory": "research",
    "Research Agent": "research",
    "Opportunity Score": "qualification",
    "Inbound Qualification": "qualification",
    "Why Now": "signal",
    "Buying Signals": "signal",
    "Trigger Plays": "trigger",
    "Website Intent": "intent",
    "Adaptive Sequences": "outreach",
    "AI Messaging": "outreach",
    "Deal Health": "deal",
    "Deal Agent": "deal",
    "Next Best Action": "crm",
    "Automatic CRM Updates": "crm",
    "Intelligent Routing": "routing",
    "Integrations": "crm",
    "Email Intelligence": "verification",
    "Revenue Graph": "revenue",
    "Revenue Analyst": "revenue",
    "Win/Loss Learning": "learning",
    "Loss Pattern Detection": "learning",
    "Autonomous Campaigns": "campaign_optimizer",
}


def test_catalog_has_exactly_the_required_parent_and_specialists():
    assert len(CATALOG) == 31
    assert len(set(CATALOG)) == 31
    assert tuple(CATALOG) == EXPECTED_IDS == CATALOG_IDS
    assert CATALOG["gtm"].dependencies == ()


def test_smart_planner_is_separate_metadata_not_a_catalog_owner():
    assert SMART_PLANNER_ENTRY.agent_id == "smart_planner"
    assert SMART_PLANNER_ENTRY.agent_id not in CATALOG
    assert SMART_PLANNER_ENTRY.capability == "pure, side-effect-free Plan IR compilation"
    assert SMART_PLANNER_ENTRY.live_status == "not_tested"


def test_entries_have_typed_contracts_bound_tools_and_honest_status():
    for agent_id, entry in CATALOG.items():
        assert entry.agent_id == agent_id
        assert entry.input_schema == f"leadx.orchestration.{agent_id}.input.v1"
        assert entry.output_schema == f"leadx.orchestration.{agent_id}.output.v1"
        assert entry.allowed_tools
        assert set(entry.allowed_tools) <= set(TOOL_NAMES)
        assert entry.executor_key == f"compatibility.{agent_id}"
        assert entry.enabled is True
        assert entry.deprecated is False
        assert entry.implementation_status == "declared"
        assert entry.live_status == "not_tested"
        assert "live/provider" not in entry.evidence


def test_responsibility_boundaries_remain_distinct():
    assert CATALOG["icp"].responsibility != CATALOG["qualification"].responsibility
    assert CATALOG["research"].responsibility != CATALOG["web_research"].responsibility
    assert CATALOG["social_intelligence"].responsibility != CATALOG["verification"].responsibility
    assert CATALOG["routing"].responsibility != CATALOG["crm"].responsibility
    assert CATALOG["intent"].allowed_tools == CATALOG["signal"].allowed_tools
    assert CATALOG["trigger"].allowed_tools == CATALOG["signal"].allowed_tools


def test_old_to_new_mapping_is_exact_and_has_no_duplicate_catalog_owners():
    assert dict(OLD_TO_NEW_MAPPING) == EXPECTED_ALIASES
    assert ALIAS_MAP is OLD_TO_NEW_MAPPING
    assert "Autonomus Campaigns" not in OLD_TO_NEW_MAPPING
    assert "Research Agent alias" not in OLD_TO_NEW_MAPPING
    assert "Prospecting Agent alias" not in OLD_TO_NEW_MAPPING
    assert OLD_TO_NEW_MAPPING["Evidence & Confidence"] == "evidence"


def test_catalog_validation_rejects_untyped_or_false_live_claims():
    original = CATALOG["research"]
    with pytest.raises(CatalogValidationError, match="unknown typed tool"):
        replace(original, allowed_tools=("provider.call",)).__post_init__()
    with pytest.raises(CatalogValidationError, match="claims live"):
        replace(original, implementation_status="live").__post_init__()
    assert validate_catalog() is CATALOG


def test_catalog_import_is_offline_and_does_not_load_runtime_clients():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
from app.orchestration.catalog import CATALOG, SMART_PLANNER_ENTRY
assert len(CATALOG) == 31
assert SMART_PLANNER_ENTRY.agent_id not in CATALOG
blocked = {name for name in sys.modules if name.startswith(('boto3', 'supabase', 'redis', 'httpx', 'dotenv'))}
assert not blocked, blocked
print('offline-catalog-import-ok')
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=root, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "offline-catalog-import-ok"
