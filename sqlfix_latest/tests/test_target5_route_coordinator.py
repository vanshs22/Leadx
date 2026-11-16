"""Deterministic task-4 route coordinator checks."""
from __future__ import annotations

import asyncio

from app.integrations.prod.discovery_runtime import route_page


def _row(fingerprint: str, source: str) -> dict:
    return {
        "id": fingerprint,
        "fingerprint": fingerprint,
        "name": f"Synthetic {fingerprint}",
        "source": source,
        "sources_hit": [source],
        "discovery_quality": 80,
        "source_url": f"https://{fingerprint}.offline.invalid",
    }


def _continuous(monkeypatch, fake, *, target=5, cancellation=None):
    from app.integrations.prod import continuous_discovery, multi_source_discovery, source_lanes

    monkeypatch.setattr(continuous_discovery, "expand_geo_labels", lambda _locations: ["cell-a", "cell-b", "cell-c"])
    monkeypatch.setattr(source_lanes, "pack_with_lane_reserves", lambda rows, *_args: rows)
    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", fake)
    return asyncio.run(continuous_discovery.discover_continuous(
        industry="gym",
        locations=["synthetic-city"],
        target_new_leads=target,
        max_cells_per_run=3,
        sources={"osm": True, "maps": True, "web": True},
        cancellation=cancellation,
    ))


def test_duplicate_routes_merge_source_provenance(monkeypatch):
    calls = 0

    async def duplicate(**kwargs):
        nonlocal calls
        calls += 1
        source = "maps" if kwargs["locations"] == ["cell-a"] else "web"
        return route_page(
            [_row("same-business", source)],
            route_cell={"source": source, "geography": kwargs["locations"][0], "query": "gym"},
            cursor={"page": 1, "token": source},
            next_cursor={"page": 2, "token": source},
            exhausted=True,
        )

    result = _continuous(monkeypatch, duplicate, target=5)
    assert calls == 3
    assert len(result) == 1
    assert set(result[0]["sources_hit"]) >= {"maps", "web"}
    assert set(result[0]["source_provenance_by_source"]) >= {"maps", "web"}


def test_cursor_loop_is_rejected_without_more_provider_calls(monkeypatch):
    from app.integrations.prod import multi_source_discovery

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("SERPER_API_KEY", "offline-fixture")
    calls = 0

    async def looping(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return route_page(
            [_row("loop", "maps")],
            route_cell={"source": "maps", "geography": "city", "query": "gym"},
            cursor={"page": 1, "token": "same"},
            next_cursor={"page": 1, "token": "same"},
            exhausted=False,
        )

    monkeypatch.setattr(multi_source_discovery, "source_maps", looping)
    result = asyncio.run(multi_source_discovery.discover_multi_source(
        "gym", ["city"],
        sources={"osm": False, "maps": True, "web": False, "yelp": False,
                 "facebook": False, "linkedin": False, "instagram": False},
        budget={"maps": 1}, target=5,
    ))
    assert calls == 1
    assert result == []
    assert any((event.get("failure") or {}).get("category") == "cursor_loop" for event in result.route_events)


def test_source_failure_does_not_discard_partial_yield(monkeypatch):
    calls = 0

    async def failing_then_partial(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic provider failure")
        return [_row(f"partial-{calls}", "web")]

    result = _continuous(monkeypatch, failing_then_partial, target=5)
    assert len(result) == 2
    assert any(event.get("event") == "failed" for event in result.telemetry["route_events"])
    assert result.telemetry["stop_reason"] == "shortfall"


def test_slow_route_cancellation_fences_inflight_call(monkeypatch):
    from app.integrations.prod import continuous_discovery, multi_source_discovery, source_lanes

    monkeypatch.setattr(continuous_discovery, "expand_geo_labels", lambda _locations: ["cell-a", "cell-b"])
    monkeypatch.setattr(source_lanes, "pack_with_lane_reserves", lambda rows, *_args: rows)
    cancellation = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def slow(**_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await cancellation.wait()
        # Leave the provider call pending after cancellation; the coordinator
        # must fence this late result before it becomes a completed task.
        await asyncio.sleep(0.01)
        return [_row("late", "web")]

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", slow)

    async def run():
        task = asyncio.create_task(continuous_discovery.discover_continuous(
            industry="gym", locations=["synthetic-city"], target_new_leads=5,
            max_cells_per_run=2, sources={"osm": True, "web": True},
            cancellation=cancellation,
        ))
        await started.wait()
        cancellation.set()
        return await task

    result = asyncio.run(run())
    assert calls == 1
    assert result == []
    assert result.telemetry["cancellation_outcome"]["fenced"] == 1
    assert result.telemetry["stop_reason"] == "cancelled"


def test_target_stop_admits_no_later_route(monkeypatch):
    calls = 0

    async def target_batch(**_kwargs):
        nonlocal calls
        calls += 1
        return [_row(f"target-{i}", "maps") for i in range(5)]

    result = _continuous(monkeypatch, target_batch, target=5)
    assert calls == 1
    assert len(result) == 5
    assert result.telemetry["stop_reason"] == "target_met"
    assert result.telemetry["provider_overrun_count"] == 0


def test_partial_yield_stays_bounded_and_truthful(monkeypatch):
    async def one(**kwargs):
        return [_row(f"only-{kwargs['locations'][0]}", "web")]

    result = _continuous(monkeypatch, one, target=5)
    assert len(result) == 3
    assert result.telemetry["shortfall"] == 2
    assert result.telemetry["queue_outcome"]["max_depth"] <= 3


def test_local_mode_makes_zero_provider_calls(monkeypatch):
    from app.integrations.prod import multi_source_discovery

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    attempted = []

    async def forbidden(*_args, **_kwargs):
        attempted.append("provider")
        raise AssertionError("provider call in local mode")

    monkeypatch.setattr(multi_source_discovery, "source_maps", forbidden)
    monkeypatch.setattr(multi_source_discovery, "source_web", forbidden)
    result = asyncio.run(multi_source_discovery.discover_multi_source(
        "gym", ["city"], sources={"osm": True, "maps": True, "web": True}, target=5,
    ))
    assert attempted == []
    assert result.contract["source"] == "local_fixture"
    assert all(row["evidence_level"] == "L0" for row in result)


def test_external_cancel_after_first_result_preserves_partial_and_fences_admission(monkeypatch):
    cancellation = asyncio.Event()
    calls = 0

    async def first_result(**_kwargs):
        nonlocal calls
        calls += 1
        cancellation.set()
        return [_row("first", "web")]

    result = _continuous(monkeypatch, first_result, target=5, cancellation=cancellation)

    assert calls == 1
    assert [lead["id"] for lead in result] == ["first"]
    assert result.telemetry["stop_reason"] == "cancelled"


def test_route_results_are_published_in_cell_order(monkeypatch):
    async def out_of_order(**kwargs):
        location = kwargs["locations"][0]
        if location == "cell-a":
            await asyncio.sleep(0.002)
        else:
            await asyncio.sleep(0)
        return [_row(location, "web")]

    result = _continuous(monkeypatch, out_of_order, target=5)

    assert [lead["id"] for lead in result] == ["cell-a", "cell-b", "cell-c"]
