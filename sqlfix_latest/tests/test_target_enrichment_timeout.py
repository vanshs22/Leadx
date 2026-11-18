import asyncio
import asyncio


def test_target_run_timeout_filters_candidate_and_finishes(monkeypatch):
    from app.integrations.prod import agent_pipeline_branches, continuous_discovery, lead6_runtime, pipeline, post_run_agent_dispatch, suppression

    monkeypatch.setenv("TARGET_ENRICH_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("LEADX_FAST_SEED", "0")  # this test fakes discover_leads; keep live free sources out of it
    calls = {"discover": 0}
    finished = []
    rejected = []
    cancelled = asyncio.Event()
    lead = {"id": "lead-timeout", "name": "Hanging Gym", "fingerprint": "timeout-fp", "source": "fake"}

    async def fake_discover(**_kwargs):
        calls["discover"] += 1
        return [dict(lead)] if calls["discover"] == 1 else []

    async def hanging_enrichment(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_specialist(result, **_kwargs):
        return result

    async def fake_prepare(result, **_kwargs):
        return result

    async def fake_run():
        return await asyncio.wait_for(
            pipeline.run_pipeline_parallel_target(
                tenant_id="tenant-a", icp_id=None, vertical="gym", geo=["Austin"], queries=None,
                limit_per_location=1, skip_enrichment=False, allow_tier_b=True,
                max_enrich_concurrent=1, deliver=False, use_crawl4ai=False, use_firecrawl=False,
                include_linkedin=False, include_instagram=False, social_limit_per_source=0,
                include_maps=False, include_web=False, include_yelp=False, include_facebook=False,
                target_assign=1, target_metric="assigned", max_cells_per_run=1, run_id="run-timeout",
                remaining=1, max_rounds=2, offer_category=None, delivery_channel="portal",
                delivery_config=None, discovery_request_id=None, min_score=0,
                execution_context={},
            ),
            timeout=2,
        )

    async def no_rows(*_args, **_kwargs):
        return []

    async def no_result(*_args, **_kwargs):
        return None

    async def fake_board(*args, **kwargs):
        if kwargs.get("stage_status") == "rejected":
            rejected.append(args[4])

    async def fake_upsert(rows):
        return rows

    async def fake_pool(rows, **_kwargs):
        return rows, rows

    async def fake_persist(*_args):
        return True

    async def fake_quota(*_args, **_kwargs):
        return 0

    async def fake_finish(_run_id, status, _summary, **_kwargs):
        finished.append(status)

    monkeypatch.setattr(pipeline, "discover_leads", fake_discover)
    monkeypatch.setattr(pipeline, "enrich_batch", hanging_enrichment)
    monkeypatch.setattr(pipeline, "_load_global_inventory_candidates", no_rows)
    monkeypatch.setattr(pipeline, "_upsert_global_batch", fake_upsert)
    monkeypatch.setattr(pipeline, "_prepare_enrichment_pool", fake_pool)
    monkeypatch.setattr(pipeline, "_persist_discovery_provider_results", fake_persist)
    monkeypatch.setattr(pipeline, "_board_upsert", fake_board)
    monkeypatch.setattr(pipeline, "_progress_run", no_result)
    monkeypatch.setattr(pipeline, "_finish_run", fake_finish)
    monkeypatch.setattr(pipeline, "_upsert_enrichment", no_result)
    monkeypatch.setattr(pipeline, "_upsert_score", no_result)
    monkeypatch.setattr(pipeline, "record_stage_transition", no_result)
    monkeypatch.setattr(pipeline, "get_supabase", lambda: object())
    monkeypatch.setattr(pipeline, "score_lead", lambda _lead, _vertical: (0, "C", "fake"))
    monkeypatch.setattr(pipeline, "passes_quality_gate", lambda *_args, **_kwargs: (False, "not_contactable"))
    monkeypatch.setattr(pipeline, "quota_remaining", fake_quota)
    monkeypatch.setattr(pipeline, "build_selection_decision", lambda **_kwargs: {
        "tier_policy": {"tier_a_target": 0}, "normalized_plan": {"tranche_size": 1}
    })
    monkeypatch.setattr(continuous_discovery, "discover_continuous", no_rows)
    monkeypatch.setattr(continuous_discovery, "mark_tenant_seen", no_result)
    monkeypatch.setattr(suppression, "filter_blocked_leads", lambda _tenant, rows: asyncio.sleep(0, result=(rows, [], [])))
    monkeypatch.setattr(lead6_runtime, "prepare_lead", fake_prepare)
    monkeypatch.setattr(agent_pipeline_branches, "enrich_lead_with_owned_agents", fake_specialist)
    monkeypatch.setattr(post_run_agent_dispatch, "dispatch_post_delivery_agents", no_rows)

    result = asyncio.run(fake_run())

    assert cancelled.is_set()
    assert finished and finished[-1] in {"failed", "partial", "completed", "cancelled"}
    assert result["stop_reason"] != "running"
    assert result["assigned_to_tenant"] == 0
    assert result["gate_rejections"].get("no_verified_contact") == 1
    assert rejected and rejected[0]["enrichment_ok"] is False
    assert rejected[0]["enrichment_error_type"] == "TimeoutError"
    assert rejected[0]["enrichment_error_category"] == "base_timeout"
    assert len(rejected[0]["enrichment_error"]) <= 64
