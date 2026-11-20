"""A full pipeline run on the local SQLite plane: the admin run payload and the run board report the same funnel."""
import asyncio
import uuid

from app.memory import local_durable


def test_sqlite_run_payload_and_board_counts_match(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    monkeypatch.setenv("LOCAL_DURABLE_BACKEND", "sqlite")
    monkeypatch.setenv("LOCAL_DURABLE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("LOCAL_AUTH_TENANT_ID", "t-board")
    monkeypatch.setenv("LEADX_PAID_FALLBACK", "never")
    local_durable.reset_local_durable_client()
    try:
        from app.memory.supabase_client import get_supabase
        from app.integrations.prod.pipeline import run_pipeline
        from app.api.prod.leads_api import get_pipeline_run, get_run_leads

        get_supabase().table("tenants").upsert({"id": "t-board", "name": "t", "status": "active", "plan": "enterprise",
                                                "leads_per_month_limit": 100000}, on_conflict="id").execute()
        run_id = str(uuid.uuid4())
        asyncio.run(run_pipeline("t-board", industry="gym", locations=["usa"], target_new_leads=5, limit_per_location=5,
                                 run_id=run_id, use_crawl4ai=False, use_firecrawl=False, deliver=True,
                                 include_web=False, include_yelp=False, include_facebook=False))

        run = get_pipeline_run(run_id)["run"]
        for key in ("gated", "crm_saved", "effective_target", "last_progress_at"):
            assert run[key] is not None, key
        assert run["effective_target"] == 5 and run["crm_saved"] == run["assigned"] == 5

        board = get_run_leads(run_id)
        assert board["stage_counts"] == {"discovered": run["discovered"], "enriched": run["enriched"],
                                         "gated": run["gated_ok"], "assigned": run["assigned"]}
        assert board["run"]["gated"] == run["gated_ok"]
    finally:
        local_durable.reset_local_durable_client()
