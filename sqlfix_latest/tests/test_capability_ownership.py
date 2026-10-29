"""Pre-fix exploration of the missing canonical capability ownership registry.

This generated-case test is intentionally expected to fail until the declarative
ownership registry is added.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3**
"""
from __future__ import annotations

import importlib
from collections.abc import Mapping

import pytest


SUPPORTED_CAPABILITIES = (
    "crm_realtime",
    "discovery",
    "enrichment",
    "agent_gtm",
    "outreach",
    "inbound_email",
    "pipeline",
)


def _field(record, *names):
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    return None


def _compatibility_paths(record):
    paths = _field(record, "compatibility_paths", "compatibility") or ()
    result = set()
    for path in paths:
        if isinstance(path, Mapping):
            value = path.get("path") or path.get("entry_point") or path.get("name")
        else:
            value = path
        if value:
            result.add(value)
    return result


@pytest.mark.parametrize("capability_id", SUPPORTED_CAPABILITIES)
def test_named_capability_has_one_canonical_owner_and_explicit_compatibility_paths(
    capability_id: str,
):
    """Every supported capability must have one discoverable ownership record.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3**
    """
    try:
        ownership = importlib.import_module("app.capabilities.ownership")
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"counterexample capability={capability_id!r}: no shared ownership "
            f"registry supplies a canonical owner or entry point ({exc})"
        )

    records = getattr(ownership, "OWNERSHIP_MAP", None)
    assert isinstance(records, Mapping), (
        f"counterexample capability={capability_id!r}: ownership map is missing"
    )
    assert set(records) == set(SUPPORTED_CAPABILITIES)

    record = records[capability_id]
    owner = _field(record, "canonical_owner", "owner_module", "owner")
    entry_point = _field(record, "canonical_entry_point", "entry_point")
    boundary = _field(record, "boundary")

    assert isinstance(owner, str) and owner.strip()
    assert isinstance(entry_point, str) and entry_point.strip()
    assert isinstance(boundary, str) and boundary.strip()
    assert _compatibility_paths(record), (
        f"counterexample capability={capability_id!r}: compatibility paths are not explicit"
    )

    if capability_id == "discovery":
        canonical_path = (
            entry_point if ":" in entry_point else f"{owner}:{entry_point}"
        )
        assert canonical_path == "app.layers.discovery_layer:discover"
        assert {"discover_for_pipeline", "app.integrations.prod.discovery:discover_leads"} <= (
            _compatibility_paths(record)
        )


@pytest.mark.parametrize(
    ("industry", "locations", "limit_per_location"),
    (
        ("dental", ["Synthetic City"], 3),
        ("gym", ["New York", "Brooklyn"], 1),
        ("roofing", [], 0),
    ),
)
def test_preservation_baseline_keeps_local_discovery_contract(
    monkeypatch, industry: str, locations: list[str], limit_per_location: int,
):
    """Record the pre-registry discovery behavior without consulting ownership metadata.

    The existing discovery and pipeline lifecycle tests remain the source of truth for
    downstream stop, deduplication, assignment, delivery, and terminal-state behavior.
    This test only records the local discovery boundary before the registry is added.

    **Validates: Requirements 2.5, 3.1, 3.2, 3.3, 3.4, 3.5**
    """
    import inspect
    import os
    import sys
    from dataclasses import fields

    from app.integrations.prod.discovery import discover_leads
    from app.integrations.prod.discovery_runtime import provider_isolation_events
    from app.layers.contracts import LayerContext, LayerResult
    from app.layers.discovery_layer import discover, discover_for_pipeline

    expected_discovery_parameters = (
        "industry", "locations", "queries", "limit_per_location",
        "include_linkedin", "include_instagram", "social_limit_per_source",
        "social_min_quality", "sources", "include_maps", "include_web",
        "include_yelp", "include_facebook", "exhaustive",
    )
    assert tuple(inspect.signature(discover_leads).parameters) == expected_discovery_parameters
    assert tuple(inspect.signature(discover).parameters) == (
        "ctx", "geo", "queries", "limit_per_location", "options",
    )
    assert tuple(inspect.signature(discover_for_pipeline).parameters) == (
        "industry", "locations", "queries", "limit_per_location", "options",
    )
    assert {field.name for field in fields(LayerResult)} == {"items", "errors", "meta"}

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    environment_before = dict(os.environ)
    isolation_events_before = tuple(provider_isolation_events())
    registry_module_before = sys.modules.get("app.capabilities.ownership")

    async def run_calls():
        queries = [f"{industry} in {locations[0] if locations else 'Local'}"]
        direct = await discover_leads(
            industry, locations, queries=queries,
            limit_per_location=limit_per_location,
        )
        layered = await discover(
            LayerContext(tenant_id="baseline-tenant", vertical=industry),
            geo=locations, queries=queries, limit_per_location=limit_per_location,
        )
        compatibility = await discover_for_pipeline(
            industry, locations, queries=queries,
            limit_per_location=limit_per_location,
        )
        return direct, layered, compatibility

    import asyncio

    direct, layered, compatibility = asyncio.run(run_calls())

    assert isinstance(layered, LayerResult)
    assert layered.items == direct == compatibility
    assert layered.errors == []
    assert layered.meta == {"layer": "discovery", "count": len(direct)}
    for row in direct:
        assert row["source"] == "local_fixture"
        assert row["source_mode"] == "local"
        assert row["billable"] is False
        assert row["source_provenance"]["billable"] is False
    assert os.environ == environment_before
    assert tuple(provider_isolation_events()) == isolation_events_before
    # Pre-fix baseline: this test must not import or execute an ownership lookup.
    assert sys.modules.get("app.capabilities.ownership") is registry_module_before
