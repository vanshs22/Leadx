"""Focused offline task-6 route execution checks."""
from __future__ import annotations

import asyncio


def _lead(name: str, source: str, fingerprint: str) -> dict:
    return {
        "name": name,
        "company_name": name,
        "source": source,
        "sources_hit": [source],
        "fingerprint": fingerprint,
        "discovery_quality": 80,
        "website": f"https://{fingerprint}.test",
        "website_domain": f"{fingerprint}.test",
        "city": "New Delhi",
        "industry": "gym",
    }


def test_route_cells_execute_free_first_preserve_duplicates_and_failures(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("SERPER_API_KEY", "offline-fixture")
    monkeypatch.setenv("LEADX_DISCOVERY_REMAINING_QUOTA", "20")
    calls: list[str] = []

    async def osm(*_args, **_kwargs):
        calls.append("osm")
        return []

    async def maps(*_args, **_kwargs):
        calls.append("maps")
        return [_lead("Shared", "maps", "shared"), _lead("Maps only", "maps", "maps")]

    async def web(*_args, **_kwargs):
        calls.append("web")
        return [_lead("Shared", "web", "shared"), _lead("Web only", "web", "web")]

    async def yelp(*_args, **_kwargs):
        calls.append("yelp")
        raise RuntimeError("fixture route failure")

    monkeypatch.setattr("app.integrations.prod.exhaustive_discovery.osm_exhaustive", osm)
    monkeypatch.setattr(module, "source_maps", maps)
    monkeypatch.setattr(module, "source_web", web)
    monkeypatch.setattr(module, "source_yelp", yelp)

    result = asyncio.run(module.discover_multi_source(
        "gym", ["Central Delhi"], queries=["gym"],
        sources={"osm": True, "maps": True, "web": True, "yelp": True,
                 "facebook": False, "linkedin": False, "instagram": False},
        budget={"osm": 2, "maps": 2, "web": 2, "yelp": 1},
        target=5,
    ))

    assert calls == ["osm", "maps", "web", "yelp"]
    assert {row["fingerprint"] for row in result} == {"shared", "maps", "web"}
    shared = next(row for row in result if row["fingerprint"] == "shared")
    assert set(shared["sources_hit"]) == {"maps", "web"}
    assert set(shared["source_provenance_by_source"]) == {"maps", "web"}
    assert all(row.get("route_cell_id") and row.get("dedupe_key") for row in result)
    assert any(event["event"] == "failed" and event["source"] == "yelp" for event in result.route_events)
    assert all("budget" in event and "cursor" in event for event in result.route_events)


def test_cursor_loop_is_recorded_and_does_not_admit_more_work(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module
    from app.integrations.prod.discovery_runtime import route_page

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("SERPER_API_KEY", "offline-fixture")
    calls: list[str] = []

    async def maps(*_args, **_kwargs):
        calls.append("maps")
        return route_page(
            [_lead("Loop", "maps", "loop")],
            route_cell={"source": "maps", "geography": "Central Delhi", "query": "gym", "result_budget": 1},
            cursor={"page": 1, "token": "same"},
            next_cursor={"page": 1, "token": "same"},
            exhausted=False,
        )

    monkeypatch.setattr(module, "source_maps", maps)
    result = asyncio.run(module.discover_multi_source(
        "gym", ["Central Delhi"], queries=["gym"],
        sources={"osm": False, "maps": True, "web": False, "yelp": False,
                 "facebook": False, "linkedin": False, "instagram": False},
        budget={"maps": 1}, target=5,
    ))

    assert calls == ["maps"]
    assert result == []
    failures = [event for event in result.route_events if event["event"] == "failed"]
    assert failures and failures[0]["failure"]["category"] == "cursor_loop"


def test_no_result_shortfall_records_invocation_without_claiming_target(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("SERPER_API_KEY", "offline-fixture")

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.integrations.prod.exhaustive_discovery.osm_exhaustive", empty)
    monkeypatch.setattr(module, "source_maps", empty)
    result = asyncio.run(module.discover_multi_source(
        "gym", ["Central Delhi", "South Delhi"], queries=["gym"],
        sources={"osm": True, "maps": True, "web": False, "yelp": False,
                 "facebook": False, "linkedin": False, "instagram": False},
        budget={"osm": 1, "maps": 1}, target=5,
    ))

    assert result == []
    assert result.contract["stop_reason"] == "shortfall"
    assert len(result.contract["route_cells"]) == 4
    assert sum(event["normalized_result_count"] for event in result.route_events if event["event"] == "invoked") == 0


def test_local_mode_expands_cells_without_provider_calls(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    attempted: list[str] = []

    async def forbidden(*_args, **_kwargs):
        attempted.append("provider")
        raise AssertionError("provider called in local mode")

    monkeypatch.setattr("app.integrations.prod.exhaustive_discovery.osm_exhaustive", forbidden)
    monkeypatch.setattr(module, "source_maps", forbidden)
    monkeypatch.setattr(module, "source_web", forbidden)
    result = asyncio.run(module.discover_multi_source(
        "gym", ["Central Delhi", "South Delhi"], queries=["gym"],
        sources={"osm": True, "maps": True, "web": True, "yelp": False,
                 "facebook": False, "linkedin": False, "instagram": False},
        budget={"maps": 1}, target=5,
    ))

    assert attempted == []
    assert len(result.contract["route_cells"]) == 2
    assert len(result) == 2
    assert all(row["source"] == "local_fixture" and row["evidence_level"] == "L0" for row in result)


def test_durable_target_stop_prevents_route_admission(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    called: list[str] = []

    async def forbidden(*_args, **_kwargs):
        called.append("provider")
        raise AssertionError("target-complete run admitted a provider")

    monkeypatch.setattr(module, "source_maps", forbidden)
    result = asyncio.run(module.discover_multi_source(
        "gym", ["Central Delhi"], sources={"osm": False, "maps": True},
        budget={"maps": 1}, target=5, assignment_confirmed=5,
    ))

    assert called == []
    assert result == []
    assert result.contract["stop_reason"] == "target_met"
