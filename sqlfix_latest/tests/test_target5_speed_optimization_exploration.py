"""Standalone pre-remediation exploration for target-speed bugs.

This file deliberately asserts the speed contract from the target5-speed-optimization
 design against the current implementation.  Failures are counterexamples for the
unfixed code.  Every fixture is deterministic and in-memory: no environment files,
credentials, network, provider, remote SQL, email, outreach, campaigns, Stripe, or
SES paths are used.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest


TARGETS = (5, 10, 50)
REQUIRED_EVIDENCE_FIELDS = {
    "target_size",
    "target_metric",
    "stage_timings",
    "route_source_counts",
    "enrichment_mode",
    "queue_outcome",
    "cancellation_outcome",
    "authoritative_counts",
    "failures",
    "stop_reason",
    "provider_overrun_count",
    "evidence_class",
}


@dataclass
class FakeClock:
    """Monotonic synthetic clock; advancing it never sleeps or performs I/O."""

    value: float = 0.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


@dataclass(frozen=True)
class SyntheticRouteCell:
    route_cell_id: str
    source: str
    cursor: str | None
    next_cursor: str | None
    budget: int


@dataclass(frozen=True)
class CacheRow:
    lead_id: str
    email: str
    email_provenance: dict[str, Any]


@dataclass
class SyntheticBatchStore:
    """Batch-effect ledger used to expose missing tenant/idempotency admission."""

    effects: list[dict[str, Any]] = field(default_factory=list)

    def record(self, *, tenant_ids: set[str], assignment_ids: list[str]) -> None:
        self.effects.append({
            "tenant_ids": set(tenant_ids),
            "assignment_ids": list(assignment_ids),
        })


@dataclass(frozen=True)
class SpeedInput:
    target: int
    eligible_route_cells: int = 3
    route_work_after_stop: bool = False
    cursor_or_provenance_lost: bool = False
    missing_deadline: bool = False
    unsafe_batch: bool = False
    incomplete_evidence: bool = False


def is_bug_condition(case: SpeedInput) -> bool:
    """Small executable form of the design's target-speed bug condition."""

    return case.target in TARGETS and (
        case.eligible_route_cells > 1
        and (case.route_work_after_stop or case.cursor_or_provenance_lost)
        or case.missing_deadline
        or case.unsafe_batch
        or case.incomplete_evidence
    )


def _route_cells() -> tuple[SyntheticRouteCell, ...]:
    return (
        SyntheticRouteCell("cell-free-a", "osm", None, "cursor-1", 2),
        SyntheticRouteCell("cell-free-b", "web", "cursor-1", "cursor-2", 2),
        SyntheticRouteCell("cell-paid", "yelp", "cursor-2", "cursor-3", 2),
    )


def _raw_route_row(index: int, *, duplicate_of: str | None = None) -> dict[str, Any]:
    fingerprint = duplicate_of or f"route-lead-{index}"
    return {
        "id": fingerprint,
        "fingerprint": fingerprint,
        "name": f"Synthetic Gym {index}",
        "source": "osm" if index == 0 else "web",
        "sources_hit": ["osm"] if index == 0 else ["web"],
        "source_url": f"https://offline.invalid/{fingerprint}",
    }


def _run_discovery(monkeypatch, *, target: int, cancel_after_first: bool, rows_per_call: int = 1):
    """Run current continuous discovery with only deterministic route fixtures."""

    from app.integrations.prod import continuous_discovery, multi_source_discovery, source_lanes

    calls: list[dict[str, Any]] = []
    cancellation = asyncio.Event()
    counter = 0

    monkeypatch.setattr(
        continuous_discovery,
        "expand_geo_labels",
        lambda _locations: [cell.route_cell_id for cell in _route_cells()],
    )
    monkeypatch.setattr(source_lanes, "annotate_lane", lambda _lead: None)
    monkeypatch.setattr(source_lanes, "pack_with_lane_reserves", lambda rows, *_args: rows)

    async def discover_multi_source(**kwargs):
        nonlocal counter
        counter += 1
        calls.append(dict(kwargs))
        if cancel_after_first:
            cancellation.set()
        return [_raw_route_row(counter * 10 + offset) for offset in range(rows_per_call)]

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", discover_multi_source)
    result = asyncio.run(
        continuous_discovery.discover_continuous(
            industry="gym",
            locations=["synthetic-city"],
            target_new_leads=target,
            max_cells_per_run=3,
            sources={"osm": True, "web": True, "yelp": True},
            assignment_confirmed=0,
            target_metric="assigned",
            cancellation=cancellation,
        )
    )
    return result, calls, cancellation


@pytest.mark.parametrize("target", TARGETS)
def test_route_target_stop_does_not_admit_work_after_durable_target(monkeypatch, target):
    """Target 5/10/50 must stop route admission at the selected target metric."""

    result, calls, _ = _run_discovery(
        monkeypatch,
        target=target,
        cancel_after_first=False,
        rows_per_call=target,
    )

    assert len(calls) == 1, f"counterexample target={target}: admitted calls={len(calls)}"
    assert len(result) == target


@pytest.mark.parametrize("target", TARGETS)
def test_route_cancellation_fences_inflight_and_queued_cells(monkeypatch, target):
    """Cancellation after the first cell must prevent every later route call."""

    result, calls, cancellation = _run_discovery(
        monkeypatch,
        target=target,
        cancel_after_first=True,
        rows_per_call=1,
    )

    assert cancellation.is_set()
    assert len(calls) == 1, (
        f"counterexample target={target}: cancellation was set but current discovery "
        f"admitted {len(calls)} route calls"
    )
    assert len(result) <= target



def test_route_result_keeps_cursor_provenance_and_duplicate_observations(monkeypatch):
    """Raw route results must leave the current discovery seam normalized and auditable."""

    from app.integrations.prod import continuous_discovery, multi_source_discovery, source_lanes

    monkeypatch.setattr(continuous_discovery, "expand_geo_labels", lambda _locations: ["cell-a", "cell-b"])
    monkeypatch.setattr(source_lanes, "annotate_lane", lambda _lead: None)
    monkeypatch.setattr(source_lanes, "pack_with_lane_reserves", lambda rows, *_args: rows)
    calls = 0

    async def duplicate_cursor_fixture(**_kwargs):
        nonlocal calls
        calls += 1
        return [_raw_route_row(1, duplicate_of="same-business")]

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", duplicate_cursor_fixture)
    result = asyncio.run(
        continuous_discovery.discover_continuous(
            industry="gym", locations=["synthetic-city"], target_new_leads=5,
            max_cells_per_run=2, sources={"osm": True, "web": True},
        )
    )

    assert calls >= 2
    assert len(result) == 1
    row = result[0]
    assert row.get("route_cell_id"), "counterexample: route cell provenance was dropped"
    assert row.get("cursor") is not None
    # The raw fixture has no provider pagination token. Preserve the field and
    # route-page default, but do not invent a next-page token.
    assert "next_cursor" in row and row["next_cursor"] is None
    assert row.get("source_provenance"), "counterexample: source provenance was dropped"
    assert row.get("source_provenance_by_source"), "counterexample: duplicate source map was dropped"



def test_enrichment_reuses_cache_and_shortlists_paid_fallback(monkeypatch):
    """A cache hit must skip free work and only unmet shortlisted leads may reach paid."""

    from app.integrations.prod import discovery_runtime
    import app.layers.enrichment_layer as enrichment_layer

    monkeypatch.setattr(discovery_runtime, "local_validation_mode", lambda: False)
    clock = FakeClock()
    free_calls: list[str] = []
    deep_calls: list[str] = []
    paid_calls: list[str] = []
    cached = CacheRow(
        "cached-lead",
        "cached@example.test",
        {"source": "synthetic-cache", "evidence_class": "synthetic-local"},
    )
    leads = [
        {
            "id": cached.lead_id,
            "email": cached.email,
            "email_valid": True,
            "email_source": cached.email_provenance,
            "phone": "+12025550101",
            "website": "https://cached.example.test",
            "owner_name": "Cached Owner",
            "linkedin_url": "https://linkedin.example.test/cached",
        },
        {"id": "unmet-lead", "name": "Needs Enrichment"},
    ]

    async def free_mesh(lead, **_kwargs):
        free_calls.append(lead["id"])
        clock.advance(0.1)
        return {"patch": {"website": f"https://{lead['id']}.example.test"}, "audit": {"mode": "free"}}

    async def free_deep(rows, **_kwargs):
        deep_calls.extend(row["id"] for row in rows)
        return rows

    async def paid_pass(lead, **_kwargs):
        paid_calls.append(lead["id"])
        return lead

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free_mesh)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", free_deep)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", paid_pass)

    rows = asyncio.run(
        enrichment_layer.enrich_for_pipeline(
            leads,
            max_concurrent=2,
            use_crawl4ai=True,
            use_firecrawl=False,
            execution_context={"metadata": {"deadline_seconds": 1.0}},
        )
    )

    assert {row["id"] for row in rows} == {"cached-lead", "unmet-lead"}
    assert free_calls == ["unmet-lead"], f"counterexample: cache was not reused: {free_calls!r}"
    assert deep_calls == ["unmet-lead"]
    assert paid_calls == ["unmet-lead"], f"counterexample: paid fallback was not shortlisted: {paid_calls!r}"
    assert all(row.get("email_source") or row.get("field_source") for row in rows)



def test_enrichment_deadline_stops_slow_free_stage_and_preserves_partial_result(monkeypatch):
    """One monotonic per-lead deadline must fence slow free work and paid escalation."""

    from app.integrations.prod import discovery_runtime
    from app.integrations.prod.provider_execution import ProviderExecutionContext
    import app.layers.enrichment_layer as enrichment_layer

    monkeypatch.setattr(discovery_runtime, "local_validation_mode", lambda: False)
    clock = FakeClock()
    paid_calls: list[str] = []
    deep_calls: list[str] = []

    async def slow_free(lead, **_kwargs):
        clock.advance(2.0)
        return {
            "patch": {"phone": "+12025550102"},
            "audit": {"mode": "free", "provenance": "synthetic-free"},
        }

    async def deep_should_not_run(rows, **_kwargs):
        deep_calls.extend(row["id"] for row in rows)
        return rows

    async def no_paid(lead, **_kwargs):
        paid_calls.append(lead["id"])
        return lead

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", slow_free)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", deep_should_not_run)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", no_paid)

    rows = asyncio.run(
        enrichment_layer.enrich_for_pipeline(
            [{"id": "slow-lead", "name": "Slow Gym"}],
            max_concurrent=1,
            execution_context=ProviderExecutionContext(
                monotonic_clock=clock.monotonic,
                metadata={"deadline_seconds": 1.0},
            ),
        )
    )

    deadline = rows[0]["enrichment_mesh_audit"]["deadline"]
    assert deadline == {"status": "deadline_exceeded", "stage": "free_mesh"}
    assert deep_calls == [], f"counterexample: deep crawl ran after deadline: {deep_calls!r}"
    assert paid_calls == [], f"counterexample: paid stage ran after deadline: {paid_calls!r}"
    assert rows[0].get("phone") == "+12025550102", "valid free partial result was lost"



def test_batch_boundary_rejects_cross_tenant_effects(monkeypatch):
    """A mixed-tenant delivery batch must fail closed before any effect."""

    from app.services import delivery

    store = SyntheticBatchStore()

    async def mark(ids, _status, _error=None, *, destination=None):
        store.record(tenant_ids={"tenant-a", "tenant-b"}, assignment_ids=ids)
        return len(ids)

    monkeypatch.setattr(delivery, "mark_delivery_status", mark)
    leads = [
        {"id": "lead-a", "tenant_id": "tenant-a"},
        {"id": "lead-b", "tenant_id": "tenant-b"},
    ]

    with pytest.raises((ValueError, PermissionError)):
        asyncio.run(
            delivery.deliver_batch(
                leads,
                "portal",
                {},
                assignment_ids=["assignment-a", "assignment-b"],
                tenant_id="tenant-a",
            )
        )
    assert not store.effects, "counterexample: mixed-tenant batch crossed the authoritative boundary"



def test_batch_retry_is_idempotent_and_reconciles_authoritative_count(monkeypatch):
    """Retrying one synthetic batch must not duplicate effects or trust optimistic length."""

    from app.services import delivery

    store = SyntheticBatchStore()

    async def mark(ids, _status, _error=None, *, destination=None):
        store.record(tenant_ids={"tenant-a"}, assignment_ids=ids)
        return 1  # authoritative store reports one persisted row, not len(ids)

    monkeypatch.setattr(delivery, "mark_delivery_status", mark)
    leads = [{"id": "lead-a", "tenant_id": "tenant-a"}, {"id": "lead-b", "tenant_id": "tenant-a"}]
    kwargs = {
        "assignment_ids": ["assignment-a", "assignment-b"],
        "tenant_id": "tenant-a",
    }

    first = asyncio.run(delivery.deliver_batch(leads, "portal", {}, **kwargs))
    second = asyncio.run(delivery.deliver_batch(leads, "portal", {}, **kwargs))

    assert first["count"] == 1 and second["count"] == 1
    assert len(store.effects) == 1, f"counterexample: retry duplicated effects: {store.effects!r}"


@pytest.mark.parametrize("target", TARGETS)
def test_timing_matrix_records_complete_sanitized_evidence(target):
    """Targets 5/10/50 need one complete report; target 500 is not a workload."""

    from app.orchestration.evidence import TargetFiveMeasurement, validate_target_five_measurement

    clock = FakeClock()
    started = clock.monotonic()
    clock.advance(0.25)
    finished = clock.monotonic()
    measurement = TargetFiveMeasurement(
        start_timestamp="2026-01-01T00:00:00+00:00",
        end_timestamp="2026-01-01T00:00:00.250000+00:00",
        elapsed_seconds=finished - started,
        environment_class="offline",
        evidence_class="synthetic/offline",
        target_metric="assigned",
        target_count=target,
        workload_boundary=f"synthetic-target-{target}",
        route_count=3,
        enrichment_mode="free-first",
        provider_mode="denied",
        counts={"assigned": target, "reserved": target, "delivered": 0},
        failures={},
        stop_reason="target_met",
        forbidden_action_counters={"email": 0, "outreach": 0, "campaign": 0, "stripe": 0, "ses": 0},
    )

    try:
        validate_target_five_measurement(measurement)
    except Exception as exc:
        pytest.fail(f"counterexample target={target}: timing matrix rejected: {type(exc).__name__}: {exc}")
    payload = measurement.to_dict()
    assert REQUIRED_EVIDENCE_FIELDS <= set(payload), (
        f"counterexample target={target}: incomplete timing schema, "
        f"missing={sorted(REQUIRED_EVIDENCE_FIELDS - set(payload))}"
    )



def test_target_500_is_only_a_cap_boundary():
    """500 is accepted as the hard cap, while 501 is rejected without a workload."""

    from pydantic import ValidationError
    from app.api.prod.leads_api import PipelineRequest

    request = PipelineRequest(tenant_id="tenant-a", target_new_leads=500)
    assert request.target_new_leads == 500
    with pytest.raises(ValidationError):
        PipelineRequest(tenant_id="tenant-a", target_new_leads=501)



def test_bug_condition_cases_are_explicit_and_offline():
    """The standalone matrix identifies every requested unfixed branch."""

    cases = (
        SpeedInput(5, route_work_after_stop=True),
        SpeedInput(10, cursor_or_provenance_lost=True),
        SpeedInput(50, missing_deadline=True),
        SpeedInput(5, unsafe_batch=True),
        SpeedInput(10, incomplete_evidence=True),
    )
    assert all(is_bug_condition(case) for case in cases)
    assert all(case.target in TARGETS for case in cases)
