import asyncio
import pytest
from datetime import datetime, timezone, timedelta


def test_pre_enrichment_dedupes_duplicate_identity_without_db(monkeypatch):
    from app.integrations.prod import pipeline

    rows = [
        {"fingerprint": "same", "name": "A", "website_domain": "a.com"},
        {"fingerprint": "same", "name": "A duplicate", "website_domain": "a.com"},
        {"fingerprint": "other", "name": "B", "website_domain": "b.com"},
    ]
    ready, needs = asyncio.run(pipeline._prepare_enrichment_pool(rows, reuse_days=14))
    assert len(ready) == 2
    assert len(needs) == 2
    assert {x["fingerprint"] for x in ready} == {"same", "other"}


def test_security_migration_revokes_direct_crm_view_access():
    from pathlib import Path
    sql = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "20261001000300_leadx_15_4_security_reliability.sql"
    text = sql.read_text()
    assert "REVOKE ALL ON TABLES FROM anon, authenticated" in text
    assert "ENABLE ROW LEVEL SECURITY" in text


def test_frontend_security_headers_are_configured():
    from pathlib import Path
    cfg = (Path(__file__).resolve().parents[1] / "../lead-crm-portal/next.config.js").resolve()
    if not cfg.is_file():
        pytest.skip("backend/core-engine release does not bundle the frontend")
    text = cfg.read_text()
    assert 'X-Content-Type-Options' in text
    assert 'X-Frame-Options' in text
    assert 'Referrer-Policy' in text


def test_target_pipeline_is_streaming_and_hard_stops_before_exhaustive_mode():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "app/integrations/prod/pipeline.py").read_text()
    start = text.index("async def run_pipeline_parallel_target(")
    end = text.index("\n\n\nasync def run_pipeline(", start)
    target = text[start:end]
    assert 'asyncio.Queue' in target
    assert 'asyncio.create_task(_enrich_worker' in target
    assert 'asyncio.create_task(_assignment_worker' in target
    assert 'stop_event.set()' in target
    assert 'if assigned_n >= slots_cap' in target
    assert 'await deliver_batch(' in target
    assert 'mode": "streaming_target"' in target
    # Zero is intentionally routed to the existing exhaustive/non-target path.
    run = text[text.index('async def run_pipeline('):]
    assert 'target_mode = int(target_new_leads or 0) > 0' in run
    assert 'if target_mode:' in run
    assert 'exhaustive_discovery = False' in run


def test_target_pipeline_supports_independent_worker_concurrency_envs():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "app/integrations/prod/pipeline.py").read_text()
    start = text.index("async def run_pipeline_parallel_target(")
    end = text.index("\n\n\nasync def run_pipeline(", start)
    target = text[start:end]
    assert 'TARGET_ENRICH_WORKERS' in target
    assert 'TARGET_ASSIGN_WORKERS' in target
    assert 'TARGET_DISCOVERY_MULTIPLIER' in target
    assert 'assign_lead(' in target


def test_shared_pool_admission_blocks_suppression_contact_and_dormant_cooldown():
    from app.integrations.prod.quality import shared_pool_admission

    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    assert shared_pool_admission({"suppressed": True}, now=now)["allowed"] is False
    assert shared_pool_admission({"last_contacted_at": "2026-10-01T00:00:00+00:00"}, now=now)["reason"] == "recently_contacted"
    dormant = shared_pool_admission(
        {"dormant": True, "dormant_since": "2026-10-20T00:00:00+00:00"},
        now=now,
    )
    assert dormant == {"allowed": False, "recheck_required": True, "reason": "dormant_cooldown"}


def test_shared_pool_loader_dedupes_and_marks_stale_candidates_for_recheck(monkeypatch):
    from app.integrations.prod import pipeline
    from conftest import FakeSupabase

    now = datetime.now(timezone.utc)
    fake = FakeSupabase({
        "leads_global": [
            {"id": "fresh", "fingerprint": "same", "category": "gym", "city": "Newark", "last_verified_at": now.isoformat(), "google_rating": 5, "review_count": 20},
            {"id": "duplicate", "fingerprint": "same", "category": "gym", "city": "Newark", "last_verified_at": now.isoformat(), "google_rating": 4, "review_count": 10},
            {"id": "stale", "fingerprint": "stale", "category": "gym", "city": "Newark", "last_verified_at": "2026-01-01T00:00:00+00:00", "google_rating": 3, "review_count": 5},
            {"id": "suppressed", "fingerprint": "suppressed", "category": "gym", "city": "Newark", "suppressed": True, "last_verified_at": now.isoformat(), "google_rating": 4, "review_count": 4},
        ],
        "lead_assignments": [],
    })
    monkeypatch.setattr(pipeline, "get_supabase", lambda: fake)

    candidates = asyncio.run(pipeline._load_global_inventory_candidates("tenant-a", "gym", ["Newark"], 20))
    assert [row["id"] for row in candidates] == ["fresh", "stale"]
    assert candidates[0]["_shared_pool_recheck_required"] is False
    assert candidates[1]["_shared_pool_recheck_required"] is True
    assert all(row["_shared_pool_candidate"] is True for row in candidates)


def test_shared_pool_assignment_requires_recheck_for_stale_candidate(monkeypatch):
    from app.integrations.prod import quality
    from conftest import FakeSupabase

    fake = FakeSupabase({"lead_assignments": []})
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)
    result = asyncio.run(quality.assign_lead(
        "stale", "tenant-a", None, "gym", ["Newark"],
        lead={
            "id": "stale", "_shared_pool_candidate": True,
            "last_verified_at": "2026-01-01T00:00:00+00:00",
            "phone": "+12015550100", "phone_status": "valid",
        },
    ))
    assert result == {"assigned": False, "reason": "recheck_required"}
