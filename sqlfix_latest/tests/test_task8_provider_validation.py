"""Task 8 offline provider-routing and latency validation.

These checks use deterministic route pages and deny-all provider behavior. They
never contact a provider, read credentials, or claim live-provider performance.
"""
from __future__ import annotations

import asyncio

import pytest

from app.integrations.prod.discovery_runtime import (
    route_failure,
    route_page,
    route_should_continue,
    stable_route_dedupe,
    typed_route_error,
)
from app.integrations.prod.provider_execution import (
    ProviderCancelled,
    ProviderExecutionContext,
    execute_provider,
)
from app.orchestration.field_evidence import (
    ContactIntegrityError,
    classify_contact,
    normalize_email,
)
from scripts.target5_speed_benchmark import run_matrix


def _lead(name: str, source: str, dedupe_key: str, *, evidence_level: str = "L1") -> dict:
    return {
        "name": name,
        "company_name": name,
        "source": source,
        "dedupe_key": dedupe_key,
        "fingerprint": dedupe_key,
        "website": f"https://{dedupe_key}.test",
        "city": "Synthetic City",
        "industry": "gym",
        "discovery_quality": 80,
        "evidence_level": evidence_level,
    }


def test_free_first_routing_gates_later_provider_lanes(monkeypatch):
    """Free contactable yield prevents paid-lane admission without live calls."""
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    calls: list[str] = []

    async def osm(*_args, **_kwargs):
        calls.append("osm")
        return route_page(
            [_lead("Free one", "osm", "free-1"), _lead("Free two", "osm", "free-2")],
            route_cell={"source": "osm", "geography": "Synthetic City", "query": "gym"},
            cursor={"page": 1, "token": None},
            exhausted=True,
        )

    async def forbidden_paid(*_args, **_kwargs):
        calls.append("paid")
        raise AssertionError("paid provider was admitted despite sufficient free yield")

    monkeypatch.setattr("app.integrations.prod.exhaustive_discovery.osm_exhaustive", osm)
    monkeypatch.setattr(module, "source_maps", forbidden_paid)
    monkeypatch.setattr(module, "source_web", forbidden_paid)

    result = asyncio.run(
        module.discover_multi_source(
            "gym",
            ["Synthetic City"],
            queries=["gym"],
            sources={
                "osm": True,
                "maps": True,
                "web": True,
                "yelp": False,
                "facebook": False,
                "linkedin": False,
                "instagram": False,
            },
            budget={"osm": 2, "maps": 2, "web": 2},
            target=5,
        )
    )

    assert calls == ["osm"]
    assert {row["dedupe_key"] for row in result} == {"free-1", "free-2"}
    blocked = [
        event
        for event in result.route_events
        if event.get("event") == "blocked" and event.get("source") in {"maps", "web"}
    ]
    assert blocked
    assert all(event["failure"]["category"] == "paid_fallback_gated" for event in blocked)


def test_yield_normalization_and_dedupe_preserve_source_provenance():
    """Accepted rows remain bounded and duplicate observations retain provenance."""
    osm = route_page(
        [_lead("Shared Gym", "osm", "shared"), _lead("OSM Only", "osm", "osm-only")],
        route_cell={"source": "osm", "geography": "Synthetic City", "query": "gym"},
        cursor={"page": 1, "token": "osm-1"},
        next_cursor={"page": 2, "token": "osm-2"},
        requested_limit=2,
    )
    web = route_page(
        [_lead("Shared Gym", "web", "shared", evidence_level="L2")],
        route_cell={"source": "web", "geography": "Synthetic City", "query": "gym"},
        cursor={"page": 1, "token": "web-1"},
        exhausted=True,
        requested_limit=1,
    )

    merged = stable_route_dedupe([*osm, *web])

    assert len(merged) == 2
    shared = next(row for row in merged if row["dedupe_key"] == "shared")
    assert set(shared["sources_hit"]) == {"osm", "web"}
    assert {"osm", "web"} <= set(shared["source_provenance_by_source"])
    assert shared["evidence_level"] == "L2"
    assert all(row["route_cell_id"] and row["raw_hash"] for row in merged)
    assert all(row["actual_result_count"] <= row["requested_limit"] for row in [*osm, *web])


def test_malformed_contact_values_fail_closed():
    """Social URLs and malformed values never become email evidence."""
    for value in ("https://instagram.com/example", "owner@", "+1 555 0100"):
        with pytest.raises(ContactIntegrityError):
            normalize_email(value)
        status, reason = classify_contact(value, field_name="email", verified=True)
        assert status == "invalid"
        assert reason


def test_provider_failure_timeout_and_cancellation_are_sanitized_and_bounded():
    """Failure categories and provider fences remain safe under deterministic faults."""
    failure = route_failure(
        route_cell={"source": "sandbox", "geography": "Synthetic City", "query": "gym"},
        source="sandbox",
        error=RuntimeError("provider payload must not escape"),
        fallback_reason="sandbox_failure",
    )
    assert failure.contract["typed_error"] == {
        "category": "provider_error",
        "retryable": True,
        "fallback_reason": "sandbox_failure",
    }
    assert "payload" not in str(failure.contract)
    assert typed_route_error("request timeout", fallback_reason="timeout")['category'] == "timeout"

    calls: list[str] = []

    async def slow_provider():
        calls.append("started")
        await asyncio.sleep(1)
        return {"ok": True}

    async def run_faults():
        context = ProviderExecutionContext(tenant_id="synthetic-tenant")
        with pytest.raises(asyncio.TimeoutError):
            await execute_provider("sandbox", slow_provider, context=context, timeout_s=0.001)

        cancelled = asyncio.Event()
        cancelled.set()
        blocked_context = ProviderExecutionContext(
            tenant_id="synthetic-tenant", cancellation_event=cancelled
        )
        with pytest.raises(ProviderCancelled):
            await execute_provider("sandbox", slow_provider, context=blocked_context, timeout_s=1)

    asyncio.run(run_faults())
    assert calls == ["started"]


def test_stop_fence_blocks_target_and_cancelled_route_admission():
    stop = asyncio.Event()
    stop.set()

    assert not route_should_continue(
        cancellation=stop,
        target=5,
        assignment_confirmed=0,
        budget_remaining=10,
        cursor={"page": 1},
        next_cursor={"page": 2},
    )
    assert not route_should_continue(
        target=5,
        assignment_confirmed=5,
        budget_remaining=10,
        cursor={"page": 1},
        next_cursor={"page": 2},
    )
    assert not route_should_continue(
        target=5,
        assignment_confirmed=0,
        budget_remaining=0,
        cursor={"page": 1},
        next_cursor={"page": 2},
    )


def test_synthetic_latency_evidence_stays_offline_and_complete():
    report = run_matrix()

    assert report["evidence_class"] == "synthetic/offline"
    assert report["provider_calls"] == 0
    assert report["production_speed_claim"] is False
    assert report["latency_claim"] is None
    assert report["cap_validation"]["executed"] is False
    assert report["forbidden_action_counters"] == {
        "email": 0,
        "outreach": 0,
        "campaign": 0,
        "stripe": 0,
        "ses": 0,
    }
    for run in report["runs"]:
        measurement = run["measurement"]
        assert set(measurement["stage_timings"]) == {
            "discovery", "enrichment", "persistence", "assignment", "delivery",
        }
        assert measurement["provider_outcome"] == {"status": "denied", "calls": 0, "overrun": 0}
        assert measurement["cancellation_outcome"]["late_writes"] == 0
        assert measurement["stop_reason"] == "target_met"
        assert run["total_elapsed_seconds"] >= 0
