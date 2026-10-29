from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime

import pytest

from app.integrations.prod import enricher
from app.integrations.prod import enrichment_mesh
from app.integrations.prod import waterfall
from app.integrations.prod.provider_execution import ProviderExecutionContext
from app.layers import enrichment_layer


async def _no_network(_url):
    return None


def test_bounded_scrapling_crawl4ai_path_keeps_valid_partial_fields_after_crawl_failure(monkeypatch):
    async def scrapling(_url, timeout=20):
        return {
            "ok": True,
            "method": "scrapling",
            "html": '<a href="mailto:owner@fixture.test">owner@fixture.test</a>',
            "text": "owner@fixture.test",
        }

    async def crawl4ai(_url):
        raise RuntimeError("synthetic crawl failure")

    monkeypatch.setattr(enricher, "fetch_scrapling", scrapling)
    monkeypatch.setattr(enricher, "fetch_crawl4ai", crawl4ai)
    monkeypatch.setattr(enricher, "_fetch_secondary_pages", _no_network)
    monkeypatch.setenv("ENRICH_SEARCH_FALLBACK", "0")

    result = asyncio.run(enricher.enrich_one("https://fixture.test", use_crawl4ai=True, use_firecrawl=False))

    assert result["email"] == "owner@fixture.test"
    assert result["email_valid"] is True
    assert result["enrichment_ok"] is True
    assert result["enrichment_method"] == "scrapling"


def test_free_mesh_records_field_source_and_freshness_for_bounded_pages(monkeypatch):
    async def scrapling(_url, timeout=20):
        return {"ok": True, "method": "scrapling", "html": '<a href="mailto:free@fixture.test">email</a>', "text": ""}

    async def crawl4ai(_url):
        return {"ok": True, "method": "crawl4ai", "html": "", "text": "", "markdown": "Services"}

    monkeypatch.setattr(enrichment_mesh, "assert_public_url", _no_network)
    monkeypatch.setattr(enrichment_mesh, "fetch_scrapling", scrapling)
    monkeypatch.setattr(enrichment_mesh, "fetch_crawl4ai", crawl4ai)

    result = asyncio.run(
        enrichment_mesh.enrich_with_free_mesh({"website": "https://fixture.test"}, use_crawl4ai=True)
    )
    patch = result["patch"]

    assert patch["email"] == "free@fixture.test"
    assert patch["field_source"]["email"] == "first_party_web"
    observed = patch["field_observed_at"]["email"]
    assert datetime.fromisoformat(observed).tzinfo is not None
    assert result["audit"]["free_mesh"]["meta"]["scrapling"]["method"] == "scrapling"
    assert result["audit"]["free_mesh"]["meta"]["crawl4ai"]["method"] == "crawl4ai"


@pytest.mark.parametrize(
    ("mode", "credentials", "fields", "lead", "quota", "reason"),
    [
        ("never", True, "email", {}, 1, "paid_fallback_disabled"),
        ("auto", False, "email", {}, 1, "paid_credential_missing"),
        ("auto", True, "email", {"email": "owner@fixture.test"}, 1, "qualifying_gap_closed"),
        ("auto", True, "email", {}, 0, "quota_exhausted"),
    ],
)
def test_pipeline_paid_stage_requires_policy_credentials_gap_and_quota(
    monkeypatch, mode, credentials, fields, lead, quota, reason
):
    calls = []

    async def free_mesh(_lead, **_kwargs):
        return {"patch": {}, "audit": {"free_mesh": {"status": "ok"}}}

    async def no_crawl(rows, **_kwargs):
        return rows

    async def paid_connector(*_args, **_kwargs):
        calls.append("paid")
        return {"patch": {"email": "paid@fixture.test"}, "providers_attempted": ["fixture"]}

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", mode)
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", fields)
    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free_mesh)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", no_crawl)
    monkeypatch.setattr(enrichment_layer, "enrich_with_paid_connectors", paid_connector)
    monkeypatch.setattr(
        enrichment_layer,
        "configured_enrichment_providers",
        lambda: [SimpleNamespace(name="apollo")] if credentials else [],
    )
    monkeypatch.setattr(enrichment_layer, "_provider_endpoint", lambda _name: "http://fixture" if credentials else None)

    result = asyncio.run(
        enrichment_layer.enrich(
            [{"website": "https://fixture.test", **lead}],
            execution_context=ProviderExecutionContext(budget_remaining=quota),
        )
    )[0]

    assert calls == []
    assert result["enrichment_mesh_audit"]["paid_gate"]["reason"] == reason


def test_pipeline_preserves_free_fields_and_provenance_when_paid_stage_fails(monkeypatch):
    async def free_mesh(_lead, **_kwargs):
        return {
            "patch": {
                "email": "free@fixture.test",
                "field_source": {"email": "first_party_web"},
                "field_confidence": {"email": 0.78},
                "field_observed_at": {"email": "2026-01-01T00:00:00+00:00"},
            },
            "audit": {"free_mesh": {"status": "ok"}},
        }

    async def no_crawl(rows, **_kwargs):
        return rows

    async def failing_paid(*_args, **_kwargs):
        raise RuntimeError("synthetic paid failure")

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", "email,phone")
    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free_mesh)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", no_crawl)
    monkeypatch.setattr(enrichment_layer, "enrich_with_paid_connectors", failing_paid)
    monkeypatch.setattr(enrichment_layer, "configured_enrichment_providers", lambda: [SimpleNamespace(name="apollo")])
    monkeypatch.setattr(enrichment_layer, "_provider_endpoint", lambda _name: "http://fixture")

    result = asyncio.run(
        enrichment_layer.enrich(
            [{"website": "https://fixture.test"}],
            execution_context=ProviderExecutionContext(budget_remaining=1),
        )
    )[0]

    assert result["email"] == "free@fixture.test"
    assert result["field_source"]["email"] == "first_party_web"
    assert result["field_observed_at"]["email"] == "2026-01-01T00:00:00+00:00"
    assert result["enrichment_mesh_audit"]["paid_stage"] == {"status": "error", "error": "RuntimeError"}


def test_waterfall_keeps_free_value_and_records_observed_at_after_later_failure():
    calls = []

    async def free(_lead, _field):
        calls.append("free")
        return {"value": "free@fixture.test", "confidence": 0.7}

    async def paid(_lead, _field):
        calls.append("paid")
        raise RuntimeError("synthetic paid failure")

    engine = waterfall.WaterfallEngine(
        [
            waterfall.Provider("free_fixture", free, ("email",), cost=0.0),
            waterfall.Provider("paid_fixture", paid, ("email",), cost=1.0, paid=True),
        ],
        accept_confidence=0.95,
        budget_per_lead=2,
    )

    result = asyncio.run(engine.enrich({}, ["email"]))

    assert calls == ["free", "paid"]
    assert result["patch"]["email"] == "free@fixture.test"
    assert result["patch"]["field_source"]["email"] == "free_fixture"
    assert datetime.fromisoformat(result["patch"]["field_observed_at"]["email"]).tzinfo is not None
    assert result["audit"]["email"]["attempts"][-1]["status"].startswith("error:")


def test_paid_mesh_records_field_freshness_without_live_provider(monkeypatch):
    async def fake_apollo(_lead, _fields):
        return {"email": "paid@fixture.test"}

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("APOLLO_API_KEY", "offline-fixture-key")
    monkeypatch.setenv("LEADX_PROVIDER_APOLLO_URL", "http://fixture.invalid")
    monkeypatch.setattr(
        enrichment_mesh,
        "configured_enrichment_providers",
        lambda: [SimpleNamespace(name="apollo", env_key="APOLLO_API_KEY")],
    )
    monkeypatch.setattr(enrichment_mesh, "_native_apollo", fake_apollo)

    result = asyncio.run(
        enrichment_mesh.enrich_with_paid_connectors(
            {"website": "https://fixture.test"}, ["email"], max_providers=1
        )
    )

    assert result["providers_attempted"] == ["apollo"]
    assert result["patch"]["email"] == "paid@fixture.test"
    assert datetime.fromisoformat(result["patch"]["field_observed_at"]["email"]).tzinfo is not None


def test_mesh_does_not_bypass_zero_context_quota(monkeypatch):
    async def free_mesh(_lead, **_kwargs):
        return {"patch": {}, "audit": {"free_mesh": {"status": "ok"}}}

    async def forbidden_paid(*_args, **_kwargs):
        raise AssertionError("paid provider called with zero quota")

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", "email")
    monkeypatch.setattr(enrichment_mesh, "enrich_with_free_mesh", free_mesh)
    monkeypatch.setattr(enrichment_mesh, "enrich_with_paid_connectors", forbidden_paid)
    monkeypatch.setattr(
        enrichment_mesh,
        "configured_enrichment_providers",
        lambda: [SimpleNamespace(name="apollo")],
    )
    monkeypatch.setattr(enrichment_mesh, "_provider_endpoint", lambda _name: "http://fixture")

    result = asyncio.run(
        enrichment_mesh.enrich_intelligent(
            {"website": "https://fixture.test"},
            wanted=["email"],
            execution_context=ProviderExecutionContext(budget_remaining=0),
        )
    )

    assert result["providers_attempted"] == []
    assert result["audit"]["paid_gate"]["reason"] == "quota_exhausted"


@pytest.mark.asyncio
async def test_paid_connector_rejects_social_url_for_email(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("APOLLO_API_KEY", "offline-fixture-key")
    monkeypatch.setenv("LEADX_PROVIDER_APOLLO_URL", "http://fixture.invalid")
    monkeypatch.setattr(
        enrichment_mesh,
        "configured_enrichment_providers",
        lambda: [SimpleNamespace(name="apollo", env_key="APOLLO_API_KEY")],
    )

    async def fake_apollo(_lead, _fields):
        return {"email": "https://instagram.com/example"}

    monkeypatch.setattr(enrichment_mesh, "_native_apollo", fake_apollo)
    result = await enrichment_mesh.enrich_with_paid_connectors(
        {"website": "https://fixture.test"}, ["email"], max_providers=1
    )

    assert "email" not in result["patch"]
    assert result["audit"]["rejected_fields"] == [{
        "provider": "apollo", "field": "email", "reason": "invalid_field_value",
    }]


@pytest.mark.asyncio
async def test_free_mesh_replaces_malformed_existing_email_with_valid_free_evidence(monkeypatch):
    async def scrapling(_url, timeout=20):
        return {"ok": True, "method": "scrapling", "html": "fixture", "text": "fixture"}

    async def crawl4ai(_url):
        return {"ok": True, "method": "crawl4ai", "html": "", "text": "", "markdown": ""}

    monkeypatch.setattr(enrichment_mesh, "assert_public_url", _no_network)
    monkeypatch.setattr(enrichment_mesh, "fetch_scrapling", scrapling)
    monkeypatch.setattr(enrichment_mesh, "fetch_crawl4ai", crawl4ai)
    monkeypatch.setattr(
        enrichment_mesh,
        "_extract_free_facts",
        lambda *_args: {"email": "owner@fixture.test", "phone": "+1 201 555 0101"},
    )

    result = await enrichment_mesh.enrich_with_free_mesh(
        {"website": "https://fixture.test", "email": "https://instagram.com/example"}
    )

    assert result["patch"]["email"] == "owner@fixture.test"
    assert result["patch"]["field_source"]["email"] == "first_party_web"


@pytest.mark.asyncio
async def test_scrapling_and_crawl4ai_start_concurrently_with_bounded_fetches(monkeypatch):
    from app.integrations.prod import enricher

    started = []
    release = asyncio.Event()

    async def scrapling(_url, timeout=20):
        started.append("scrapling")
        await release.wait()
        return {"ok": True, "method": "scrapling", "html": "", "text": ""}

    async def crawl4ai(_url):
        started.append("crawl4ai")
        await release.wait()
        return {"ok": True, "method": "crawl4ai", "html": "", "text": "", "markdown": ""}

    monkeypatch.setattr(enricher, "fetch_scrapling", scrapling)
    monkeypatch.setattr(enricher, "fetch_crawl4ai", crawl4ai)
    monkeypatch.setattr(enricher, "_fetch_secondary_pages", _no_network)
    monkeypatch.setenv("ENRICH_SEARCH_FALLBACK", "0")

    task = asyncio.create_task(enricher.enrich_one("https://fixture.test", use_firecrawl=False))
    for _ in range(3):
        await asyncio.sleep(0)
        if len(started) == 2:
            break
    assert set(started) == {"scrapling", "crawl4ai"}
    release.set()
    result = await task
    assert result["enrichment_ok"] is False


@pytest.mark.asyncio
async def test_pipeline_merge_rejects_untyped_social_url_from_paid_stage(monkeypatch):
    async def free_mesh(_lead, **_kwargs):
        return {"patch": {}, "audit": {"free_mesh": {"status": "ok"}}}

    async def no_crawl(rows, **_kwargs):
        return rows

    async def untyped_paid(*_args, **_kwargs):
        return {"patch": {"email": "https://instagram.com/example"}, "providers_attempted": ["fixture"]}

    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "false")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    monkeypatch.setenv("LEADX_PAID_ENRICH_FIELDS", "email")
    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free_mesh)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", no_crawl)
    monkeypatch.setattr(enrichment_layer, "enrich_with_paid_connectors", untyped_paid)
    monkeypatch.setattr(enrichment_layer, "configured_enrichment_providers", lambda: [SimpleNamespace(name="fixture")])
    monkeypatch.setattr(enrichment_layer, "_provider_endpoint", lambda _name: "http://fixture")

    result = (await enrichment_layer.enrich(
        [{"website": "https://fixture.test"}],
        execution_context=ProviderExecutionContext(budget_remaining=1),
    ))[0]

    assert "email" not in result
    assert result["enrichment_mesh_audit"]["rejected_fields"] == [{
        "field": "email", "reason": "invalid_field_value",
    }]


@pytest.mark.asyncio
async def test_target_runner_persists_discovery_before_enrichment_and_overlaps_leads(monkeypatch):
    from app.integrations.prod import pipeline

    events = []
    leads = [
        {"id": "lead-stream-1", "fingerprint": "stream-1", "name": "One", "website": "https://one.example.test",
         "email": "one@example.test", "email_status": "valid", "phone": None},
        {"id": "lead-stream-2", "fingerprint": "stream-2", "name": "Two", "website": "https://two.example.test",
         "email": "two@example.test", "email_status": "valid", "phone": None},
    ]

    async def discover(**_kwargs):
        events.append("discover")
        return [dict(lead) for lead in leads]

    async def upsert_batch(rows):
        events.append("persist_global")
        return rows

    async def persist_results(_request_id, _tenant_id, _rows):
        events.append("persist_result")
        return True

    async def prepare_pool(rows, **_kwargs):
        return rows, rows

    async def enrich(rows, **_kwargs):
        events.append(f"enrich:{rows[0]['id']}")
        await asyncio.sleep(0.01)
        return [{**row, "enrichment_ok": True} for row in rows]

    async def prepare_lead(lead, **_kwargs):
        return {**lead, "quality_gate": {"eligible": True}}

    async def assign(**kwargs):
        events.append(f"assign:{kwargs['lead_id']}")
        return {"assigned": True, "assignment_id": f"assignment-{kwargs['lead_id']}", "crm_saved": True}

    async def noop(*_args, **_kwargs):
        return None

    async def quota(_tenant_id):
        return 0

    async def no_inventory(*_args, **_kwargs):
        return []

    monkeypatch.setattr(pipeline, "discover_leads", discover)
    monkeypatch.setattr(pipeline, "_load_global_inventory_candidates", no_inventory)
    monkeypatch.setattr(pipeline, "_upsert_global_batch", upsert_batch)
    monkeypatch.setattr(pipeline, "_persist_discovery_provider_results", persist_results)
    monkeypatch.setattr(pipeline, "_prepare_enrichment_pool", prepare_pool)
    monkeypatch.setattr(pipeline, "enrich_batch", enrich)
    monkeypatch.setattr(pipeline, "assign_lead", assign)
    monkeypatch.setattr(pipeline, "_upsert_enrichment", noop)
    monkeypatch.setattr(pipeline, "_upsert_score", noop)
    monkeypatch.setattr(pipeline, "_board_upsert", noop)
    monkeypatch.setattr(pipeline, "_progress_run", noop)
    monkeypatch.setattr(pipeline, "_finish_run", noop)
    monkeypatch.setattr(pipeline, "_execution_allows_write", lambda _ctx: True)
    monkeypatch.setattr(pipeline, "score_lead", lambda _lead, _vertical: (90, "A", "synthetic"))
    monkeypatch.setattr(pipeline, "passes_quality_gate", lambda *_args, **_kwargs: (True, "ok"))
    monkeypatch.setattr(pipeline, "quota_remaining", quota)

    from app.integrations.prod import lead6_runtime
    monkeypatch.setattr(lead6_runtime, "prepare_lead", prepare_lead)

    result = await pipeline.run_pipeline_parallel_target(
        tenant_id="tenant-stream",
        icp_id=None,
        vertical="gym",
        geo=["New Delhi"],
        queries=["gym"],
        limit_per_location=2,
        skip_enrichment=False,
        allow_tier_b=True,
        max_enrich_concurrent=2,
        deliver=False,
        use_crawl4ai=False,
        use_firecrawl=False,
        include_linkedin=False,
        include_instagram=False,
        social_limit_per_source=0,
        include_maps=True,
        include_web=True,
        include_yelp=False,
        include_facebook=False,
        target_assign=2,
        max_cells_per_run=2,
        run_id="run-stream",
        remaining=2,
        max_rounds=1,
        offer_category=None,
        delivery_channel="portal",
        delivery_config={},
        discovery_request_id="request-stream",
        min_score=0,
    )

    assert result["assigned_to_tenant"] == 2
    assert events.index("persist_result") < events.index("enrich:lead-stream-1")
    assert events.index("persist_result") < events.index("enrich:lead-stream-2")
    assert {event for event in events if event.startswith("enrich:")} == {
        "enrich:lead-stream-1", "enrich:lead-stream-2",
    }
