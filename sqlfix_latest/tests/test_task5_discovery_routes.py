"""Task 5.1 deterministic discovery-route evidence.

These tests call the existing bounded/continuous orchestrators with offline route
fixtures. They never load .env, contact a provider, or use Supabase.
"""
from __future__ import annotations

import asyncio


def _lead(name: str, source: str, fingerprint: str, **extra) -> dict:
    row = {
        "name": name,
        "company_name": name,
        "source": source,
        "sources_hit": [source],
        "source_ids": {source: f"fixture:{fingerprint}"},
        "source_provenance": {"source": source, "mode": "fixture", "billable": source != "osm"},
        "fingerprint": fingerprint,
        "discovery_quality": 80,
        "phone": None,
        "website": f"https://{fingerprint}.test",
        "website_domain": f"{fingerprint}.test",
        "city": "Fixture City",
        "industry": "gym",
    }
    row.update(extra)
    return row


def test_bounded_orchestrator_invokes_all_routes_free_first_with_budgets_dedupe_and_partial_failure(monkeypatch):
    from app.integrations.prod import exhaustive_discovery, multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("SERPER_API_KEY", "fixture-serper-key")
    monkeypatch.setenv("LEADX_DISCOVERY_REMAINING_QUOTA", "20")
    monkeypatch.setenv("LEADX_FREE_OSM", "1")

    calls: list[str] = []
    budgets: dict[str, int] = {}
    shared = _lead("Shared Gym", "fixture", "fp-shared")

    async def osm(industry, location):
        calls.append("osm")
        return [_lead("Shared Gym", "osm", "fp-shared", source_mode="free_osm")]

    async def maps(industry, location, queries, budget):
        calls.append("maps")
        budgets["maps"] = budget
        return [
            _lead("Shared Gym", "maps", "fp-shared"),
            _lead("Maps Gym", "maps", "fp-maps"),
        ][:budget]

    async def web(industry, location, queries, budget):
        calls.append("web")
        budgets["web"] = budget
        return [shared | {"source": "web", "sources_hit": ["web"], "source_ids": {"web": "fixture:web"}},
                _lead("Web Gym", "web", "fp-web")][:budget]

    async def yelp(industry, location, budget):
        calls.append("yelp")
        budgets["yelp"] = budget
        return [_lead("Yelp Gym", "yelp", "fp-yelp")][:budget]

    async def facebook(industry, location, budget):
        calls.append("facebook")
        budgets["facebook"] = budget
        raise RuntimeError("fixture-facebook-failure")

    async def linkedin(industry, location, budget):
        calls.append("linkedin")
        budgets["linkedin"] = budget
        return [_lead("LinkedIn Gym", "linkedin", "fp-linkedin", linkedin_url="https://linkedin.com/company/fixture")][:budget]

    async def instagram(industry, location, budget):
        calls.append("instagram")
        budgets["instagram"] = budget
        return [_lead("Instagram Gym", "instagram", "fp-instagram", instagram_handle="fixture_gym")][:budget]

    monkeypatch.setattr(exhaustive_discovery, "osm_exhaustive", osm)
    monkeypatch.setattr(module, "source_maps", maps)
    monkeypatch.setattr(module, "source_web", web)
    monkeypatch.setattr(module, "source_yelp", yelp)
    monkeypatch.setattr(module, "source_facebook", facebook)
    monkeypatch.setattr(module, "source_linkedin", linkedin)
    monkeypatch.setattr(module, "source_instagram", instagram)

    result = asyncio.run(module.discover_multi_source(
        "gym", ["Fixture City"], queries=["gym"],
        sources={name: True for name in ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")},
        budget={"osm": 1, "maps": 3, "web": 2, "yelp": 1, "facebook": 1, "linkedin": 2, "instagram": 2},
        min_quality=50,
    ))

    assert calls[0] == "osm", "free OSM must be attempted before paid/search lanes"
    assert set(calls) == {"osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram"}
    assert budgets == {"maps": 3, "web": 2, "yelp": 1, "facebook": 1, "linkedin": 2, "instagram": 2}
    # A failed Facebook lane does not erase successful route results.
    assert {row["source"] for row in result} >= {"maps", "web", "yelp", "linkedin", "instagram"}
    assert len([row for row in result if row["fingerprint"] == "fp-shared"]) == 1
    merged = next(row for row in result if row["fingerprint"] == "fp-shared")
    assert {"osm", "maps", "web"} <= set(merged["sources_hit"])
    assert all(row.get("fingerprint") and row.get("source") and row.get("sources_hit") for row in result)
    assert len(result) == len({row["fingerprint"] for row in result})


def test_decodo_is_eligible_before_serper_and_missing_paid_credentials_skip_routes(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_DISCOVERY_REMAINING_QUOTA", "5")
    monkeypatch.setenv("DECODO_API_KEY", "fixture-decodo-key")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    trace: list[str] = []

    async def decodo(query, num=10):
        trace.append("decodo")
        return [{"title": "Decodo Gym", "link": "https://decodo-gym.test", "snippet": "gym Fixture City"}]

    async def serper(query, num=10):
        trace.append("serper")
        raise AssertionError("Serper must not run when Decodo satisfies the web lane")

    monkeypatch.setattr(module, "_decodo_search", decodo)
    monkeypatch.setattr(module, "_serper_search", serper)
    rows = asyncio.run(module.discover_multi_source(
        "gym", ["Fixture City"], queries=["gym"],
        sources={"osm": False, "maps": False, "web": True, "yelp": False, "facebook": False, "linkedin": False, "instagram": False},
        budget={"web": 2},
    ))
    assert rows and rows[0]["source"] == "web"
    assert trace and set(trace) == {"decodo"}

    monkeypatch.delenv("DECODO_API_KEY", raising=False)
    trace.clear()
    rows = asyncio.run(module.discover_multi_source(
        "gym", ["Fixture City"], queries=["gym"],
        sources={"osm": False, "maps": False, "web": True, "yelp": False, "facebook": False, "linkedin": False, "instagram": False},
        budget={"web": 2},
    ))
    assert rows == []
    assert trace == [], "paid/search routes must be skipped without an eligible credential"


def test_web_route_falls_back_from_decodo_to_serper_without_live_calls(monkeypatch):
    from app.integrations.prod import multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_DISCOVERY_REMAINING_QUOTA", "5")
    monkeypatch.setenv("DECODO_API_KEY", "fixture-decodo-key")
    monkeypatch.setenv("SERPER_API_KEY", "fixture-serper-key")
    trace: list[str] = []

    async def decodo(query, num=10):
        trace.append("decodo")
        return []

    async def serper(query, num=10):
        trace.append("serper")
        return [{"title": "Serper Gym", "link": "https://serper-gym.test", "snippet": "gym Fixture City"}]

    monkeypatch.setattr(module, "_decodo_search", decodo)
    monkeypatch.setattr(module, "_serper_search", serper)
    rows = asyncio.run(module.discover_multi_source(
        "gym", ["Fixture City"], queries=["gym"],
        sources={"osm": False, "maps": False, "web": True, "yelp": False, "facebook": False, "linkedin": False, "instagram": False},
        budget={"web": 1},
    ))
    assert rows and rows[0]["name"] == "Serper Gym"
    assert trace[:2] == ["decodo", "serper"]


def test_local_validation_isolates_every_route(monkeypatch):
    from app.integrations.prod import exhaustive_discovery, multi_source_discovery as module

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    def forbidden(*args, **kwargs):
        raise AssertionError("route called in local validation mode")

    monkeypatch.setattr(exhaustive_discovery, "osm_exhaustive", forbidden)
    for name in ("source_maps", "source_web", "source_yelp", "source_facebook", "source_linkedin", "source_instagram"):
        monkeypatch.setattr(module, name, forbidden)

    rows = asyncio.run(module.discover_multi_source(
        "gym", ["Fixture City"],
        sources={name: True for name in ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")},
        budget={"maps": 2},
    ))
    assert len(rows) == 2
    assert all(row["source"] == "local_fixture" and row["billable"] is False for row in rows)


def test_discover_leads_uses_exhaustive_fallback_after_bounded_failure(monkeypatch):
    from app.integrations.prod import discovery, exhaustive_discovery, multi_source_discovery

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    calls: list[str] = []

    async def bounded_failure(*args, **kwargs):
        calls.append("bounded")
        raise RuntimeError("fixture-bounded-failure")

    async def exhaustive_fixture(*args, **kwargs):
        calls.append("fallback")
        return [_lead("Fallback Gym", "osm", "fp-fallback")]

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", bounded_failure)
    monkeypatch.setattr(exhaustive_discovery, "discover_exhaustive", exhaustive_fixture)
    result = asyncio.run(discovery.discover_leads(
        "gym", ["Fixture City"], queries=["gym"], limit_per_location=1,
        exhaustive=False,
    ))
    assert [row["name"] for row in result] == ["Fallback Gym"]
    assert calls == ["bounded", "fallback"]


def test_continuous_orchestrator_stops_at_target_and_reports_shortfall(monkeypatch):
    from app.integrations.prod import continuous_discovery as module, multi_source_discovery

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    all_sources_off = {name: name == "maps" for name in ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")}
    calls: list[str] = []

    async def target_batch(*, locations, **kwargs):
        calls.append(locations[0])
        return [_lead(f"Target Gym {i}", "maps", f"fp-target-{i}") for i in range(3)]

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", target_batch)
    result = asyncio.run(module.discover_continuous(
        industry="gym", locations=["One", "Two"], target_new_leads=2,
        max_cells_per_run=2, sources=all_sources_off,
    ))
    assert len(result) == 2
    assert calls == ["One"]
    assert result.telemetry["stop_reason"] == "target_met"
    assert result.telemetry["shortfall"] == 0

    calls.clear()

    async def shortfall_batch(*, locations, **kwargs):
        calls.append(locations[0])
        return [_lead(f"Short Gym {len(calls)}", "maps", f"fp-short-{len(calls)}")]

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", shortfall_batch)
    result = asyncio.run(module.discover_continuous(
        industry="gym", locations=["One", "Two"], target_new_leads=5,
        max_cells_per_run=2, sources=all_sources_off,
    ))
    assert len(result) == 2
    assert len(calls) == 2
    assert result.telemetry["stop_reason"] == "shortfall"
    assert result.telemetry["shortfall"] == 3
    assert result.telemetry["coverage"]["saturation"]["reason"] == "shortfall"


def test_local_mode_isolates_all_provider_families_and_stops_after_target(monkeypatch):
    """Local orchestration stays synthetic and does not enter any provider adapter."""
    from app.integrations.prod import (
        continuous_discovery,
        discovery,
        discovery_runtime,
        enricher,
        enrichment_mesh,
        exhaustive_discovery,
        multi_source_discovery,
        social_discovery,
        social_enrich,
    )
    from app.layers import enrichment_layer

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    # Credentials must not change the local boundary; these are fixture values only.
    for key in (
        "SERPER_API_KEY", "DECODO_API_KEY", "FIRECRAWL_API_KEY", "APOLLO_API_KEY",
        "HUNTER_API_KEY", "LINKEDIN_LI_AT_COOKIE",
    ):
        monkeypatch.setenv(key, "offline-fixture-key")
    discovery_runtime.clear_provider_isolation_events()

    attempted: list[str] = []

    def forbidden(*args, **kwargs):
        attempted.append("provider")
        raise AssertionError("local mode entered a provider adapter")

    boundaries = {
        multi_source_discovery: (
            "_decodo_search", "_serper_search", "_serper_maps", "source_maps", "source_web",
            "source_yelp", "source_facebook", "source_linkedin", "source_instagram",
        ),
        exhaustive_discovery: ("osm_exhaustive", "discover_exhaustive", "serper_maps_exhaustive"),
        discovery: ("_serper_maps_page", "_serper_search_page"),
        social_discovery: (
            "_serper", "get_logged_in_instaloader", "discover_linkedin_companies",
            "discover_linkedin_decision_makers", "discover_instagram_businesses",
            "discover_linkedin_via_scraper", "discover_instagram_via_instaloader",
        ),
        enricher: (
            "fetch_scrapling", "fetch_crawl4ai", "fetch_firecrawl", "_serper_contact_fallback",
            "_resolve_website",
        ),
        enrichment_mesh: (
            "_fetch_free_site", "_native_apollo", "_native_hunter", "_native_serper",
            "_generic_connector", "enrich_with_paid_connectors",
        ),
        enrichment_layer: ("enrich_batch", "enrich_intelligent", "enrich_with_paid_connectors"),
        social_enrich: ("social_enrich_batch",),
    }
    for owner, names in boundaries.items():
        for name in names:
            if hasattr(owner, name):
                monkeypatch.setattr(owner, name, forbidden)

    real_discover = multi_source_discovery.discover_multi_source
    calls: list[tuple[str, ...]] = []

    async def observed_discover(*, locations, **kwargs):
        calls.append(tuple(locations))
        return await real_discover(locations=locations, **kwargs)

    monkeypatch.setattr(multi_source_discovery, "discover_multi_source", observed_discover)
    result = asyncio.run(continuous_discovery.discover_continuous(
        industry="gym",
        locations=["One", "Two"],
        target_new_leads=1,
        max_cells_per_run=2,
        sources={name: True for name in ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")},
    ))

    assert len(result) == 1
    assert result.telemetry["stop_reason"] == "target_met"
    assert result.telemetry["shortfall"] == 0
    assert calls == [("One",)], "target completion must prevent later-cell discovery"

    enriched = asyncio.run(enrichment_layer.enrich_for_pipeline([result[0]], use_crawl4ai=True, use_firecrawl=True))
    assert enriched[0]["source_mode"] == "local"
    assert enriched[0]["billable"] is False
    assert attempted == []
    assert discovery_runtime.provider_isolation_events() == []



def test_generated_route_duplicates_keep_identity_budget_and_provenance():
    """Generated route duplicates collapse without losing source identity metadata.

    **Validates: Requirements 2.5, 2.6**
    """
    from app.integrations.prod.multi_source_discovery import merge_leads

    sources = ("maps", "osm", "web", "yelp", "facebook", "linkedin", "instagram")
    for case in range(64):
        first_source = sources[case % len(sources)]
        second_source = sources[(case + 1) % len(sources)]
        fingerprint = f"fixture-shared-{case}"
        first = _lead(f"Shared {case}", first_source, fingerprint)
        second = _lead(f"Shared {case} duplicate", second_source, fingerprint)
        first["source_provenance"] = {
            "source": first_source, "mode": "fixture", "billable": first_source != "osm",
        }
        second["source_provenance"] = {
            "source": second_source, "mode": "fixture", "billable": second_source != "osm",
        }

        merged = merge_leads([[first], [second]], min_quality=50)

        assert len(merged) == 1
        row = merged[0]
        assert row["fingerprint"] == fingerprint
        assert {first_source, second_source} <= set(row["sources_hit"])
        assert row["source_ids"][first_source].startswith("fixture:")
        assert row["source_ids"][second_source].startswith("fixture:")
        provenance = row.get("source_provenance_by_source") or {}
        assert {first_source, second_source} <= set(provenance)
        assert all(item["mode"] == "fixture" for item in provenance.values())


async def _fixture_rows(source: str, count: int, *, prefix: str = "budget") -> list[dict]:
    return [_lead(f"{source} {i}", source, f"{prefix}-{source}-{i}") for i in range(count)]


def test_generated_route_budgets_are_hard_caps_including_zero(monkeypatch):
    """Every bounded route honors its requested result budget without I/O.

    **Validates: Requirements 2.5, 2.12**
    """
    from app.integrations.prod import multi_source_discovery as module
    from app.integrations.prod import social_discovery

    async def no_sleep(*_args, **_kwargs):
        return None

    async def maps(*_args, **kwargs):
        return await _fixture_rows("maps", 12)

    async def web(*_args, **kwargs):
        return await _fixture_rows("web", 12)

    async def search(*_args, **kwargs):
        return [{"title": f"Search {i}", "link": f"https://search-{i}.test", "snippet": "gym Fixture City"}
                for i in range(12)]

    async def linkedin(*_args, **_kwargs):
        return await _fixture_rows("linkedin", 12)

    async def instagram(*_args, **_kwargs):
        return await _fixture_rows("instagram", 12)

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(module, "_serper_maps", maps)
    monkeypatch.setattr(module, "_web_search", web)
    monkeypatch.setattr(module, "_serper_search", search)
    for name in (
        "discover_linkedin_companies", "discover_linkedin_decision_makers",
        "discover_linkedin_via_scraper", "discover_instagram_businesses",
        "discover_instagram_via_instaloader",
    ):
        if hasattr(social_discovery, name):
            monkeypatch.setattr(social_discovery, name, linkedin if "linkedin" in name else instagram)

    async def run():
        observed = {}
        for budget in range(8):
            observed["maps"] = len(await module.source_maps("gym", "Fixture City", ["gym"], budget))
            observed["web"] = len(await module.source_web("gym", "Fixture City", ["gym"], budget))
            observed["yelp"] = len(await module.source_yelp("gym", "Fixture City", budget))
            observed["facebook"] = len(await module.source_facebook("gym", "Fixture City", budget))
            observed["linkedin"] = len(await module.source_linkedin("gym", "Fixture City", budget))
            observed["instagram"] = len(await module.source_instagram("gym", "Fixture City", budget))
            assert all(value <= budget for value in observed.values())

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    asyncio.run(run())



def test_generated_route_failures_preserve_successful_lanes(monkeypatch):
    """Each deterministic lane failure is isolated while other routes survive.

    **Validates: Requirements 2.5, 2.12**
    """
    from app.integrations.prod import exhaustive_discovery
    from app.integrations.prod import multi_source_discovery as module

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(exhaustive_discovery, "osm_exhaustive", lambda *_args, **_kwargs: _fixture_rows("osm", 0))
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("SERPER_API_KEY", "fixture-key")
    monkeypatch.setenv("LEADX_DISCOVERY_REMAINING_QUOTA", "50")
    route_names = ("maps", "web", "yelp", "facebook", "linkedin", "instagram")

    async def run():
        for case in range(24):
            failed = route_names[case % len(route_names)]
            calls = []

            async def route(name, *_args, **_kwargs):
                calls.append(name)
                if name == failed:
                    raise RuntimeError(f"fixture-{name}-failure")
                return await _fixture_rows(name, 1, prefix=f"failure-{case}")

            monkeypatch.setattr(module, "source_maps", lambda *a, **k: route("maps", *a, **k))
            monkeypatch.setattr(module, "source_web", lambda *a, **k: route("web", *a, **k))
            monkeypatch.setattr(module, "source_yelp", lambda *a, **k: route("yelp", *a, **k))
            monkeypatch.setattr(module, "source_facebook", lambda *a, **k: route("facebook", *a, **k))
            monkeypatch.setattr(module, "source_linkedin", lambda *a, **k: route("linkedin", *a, **k))
            monkeypatch.setattr(module, "source_instagram", lambda *a, **k: route("instagram", *a, **k))

            result = await module.discover_multi_source(
                "gym", ["Fixture City"], queries=["gym"],
                sources={"osm": False, **{name: True for name in route_names}},
                budget={name: 1 for name in route_names}, min_quality=50,
            )
            assert failed in calls
            assert result
            assert all(row["fingerprint"] for row in result)
            assert all(row["source"] != failed for row in result)

    asyncio.run(run())



def test_generated_contact_values_never_override_verification_safety(monkeypatch):
    """Malformed values and non-usable verification states stay rejected.

    **Validates: Requirements 2.6, 2.12**
    """
    from app.integrations.prod.contact_state import email_level, email_reason
    from app.integrations.prod.quality import passes_quality_gate

    malformed = (
        "https://instagram.com/fixture", "@fixture", "name@@fixture.test",
        "owner@fixture..test", "owner fixture.test", "owner@fixture",
    )
    invalid_statuses = ("invalid", "undeliverable", "do_not_mail", "spamtrap", "unverified", "unknown")
    for case in range(48):
        value = malformed[case % len(malformed)]
        status = invalid_statuses[case % len(invalid_statuses)]
        lead = {
            "tier": "A", "website": "https://fixture.test", "address": "1 Main",
            "services": ["gym"], "email": value, "email_status": status,
            "email_valid": True,
        }
        assert email_reason(lead) == "invalid"
        assert email_level(lead) is None
        assert passes_quality_gate(lead) == (False, "not_contactable")

    for status in invalid_statuses:
        lead = {
            "email": "owner@fixture.test", "website": "https://fixture.test",
            "email_status": status, "email_valid": True,
        }
        assert email_reason(lead) in {"invalid", "unavailable"}



def test_generated_target_stop_and_shortfall_telemetry_are_truthful(monkeypatch):
    """Target completion stops later cells; exhausted fixtures report shortfall.

    **Validates: Requirements 2.5, 2.6**
    """
    from app.integrations.prod import continuous_discovery as module
    from app.integrations.prod import multi_source_discovery

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    sources = {name: name == "maps" for name in ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")}

    async def run():
        for target in range(1, 8):
            calls = []

            async def target_batch(*, locations, **_kwargs):
                calls.append(locations[0])
                return await _fixture_rows("maps", target + 2, prefix=f"target-{target}")

            monkeypatch.setattr(multi_source_discovery, "discover_multi_source", target_batch)
            result = await module.discover_continuous(
                industry="gym", locations=[f"Cell-{target}-A", f"Cell-{target}-B"],
                target_new_leads=target, max_cells_per_run=2, sources=sources,
            )
            assert len(result) == target
            assert result.telemetry["stop_reason"] == "target_met"
            assert result.telemetry["shortfall"] == 0
            assert len(calls) == 1

            calls.clear()

            async def shortfall_batch(*, locations, **_kwargs):
                calls.append(locations[0])
                return await _fixture_rows("maps", 1, prefix=f"shortfall-{target}-{len(calls)}")

            monkeypatch.setattr(multi_source_discovery, "discover_multi_source", shortfall_batch)
            result = await module.discover_continuous(
                industry="gym", locations=[f"Short-{target}-A", f"Short-{target}-B"],
                target_new_leads=target, max_cells_per_run=2, sources=sources,
            )
            assert len(result) <= target
            assert result.telemetry["stop_reason"] == ("target_met" if len(result) == target else "shortfall")
            assert result.telemetry["shortfall"] == target - len(result)
            assert len(calls) <= target

    asyncio.run(run())
