from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_run_creation_is_durable_and_queued_with_legacy_fallback():
    text = (ROOT / "app/api/prod/leads_api.py").read_text()
    start = text.index('async def run_pipeline(req: PipelineRequest')
    end = text.index('\n\n@router.post("/pipeline/run-all-icps")', start)
    block = text[start:end]
    assert '"status": "queued"' in block
    assert 'allow_tier_c' in block
    assert 'legacy_row = {**run_row, "status": "running"}' in block
    assert 'same durable row identity' in block
    assert 'pipeline_runs INSERT returned no readable row' in block
    assert 'Pipeline run could not be created in Supabase' in block
    assert 'pre-create pipeline_runs failed (continuing)' not in block


def test_frontend_authoritative_run_hydration_and_terminal_contract():
    text = (ROOT / "lead-crm-portal/app/(admin)/admin/pipeline/page.tsx").read_text()
    assert 'params.get("run_id")' in text
    assert 'setLiveRunId(String(rid))' in text
    assert 'setLiveRun({' not in text
    assert 'admin.getRun(liveRunId)' in text
    assert 'partial' in text
    assert 'setBusy(false)' in text
    assert 'was not found. Refresh or start a new pipeline run.' in text
    assert 'min={1}' in text
    assert 'target_new_leads: 5' in text
    assert 'allow_tier_c' in text
    assert 'Math.max(1, Number(e.target.value) || 1)' in text


def test_pipeline_executor_requires_run_row_persistence():
    text = (ROOT / "app/integrations/prod/pipeline.py").read_text()
    start = text.index('async def run_pipeline(')
    end = text.index('\n    vertical = industry', start)
    block = text[start:end]
    assert 'pipeline_runs lifecycle write was not readable after persistence' in block
    assert 'CRITICAL pipeline run persistence failed' in block
    assert 'duplicate' in block and 'different tenant' in block
    assert 'non-fatal fallback exception' not in block


def test_pipeline_queue_race_is_reconciled_in_api_and_worker():
    api = (ROOT / "app/api/prod/leads_api.py").read_text()
    worker = (ROOT / "app/worker.py").read_text()
    assert 'row was created by worker' in api
    assert '_reconcile_pipeline_run_failure' in worker
    assert '"queued", "running"' in worker
    jobs = (ROOT / "app/integrations/prod/jobs.py").read_text()
    assert 'pipeline_runs' in jobs and 'accounting_reconciliation_status' in jobs


def test_pipeline_runs_support_queued_lifecycle_on_existing_databases():
    migration = (ROOT / "supabase/migrations/20261001000700_leadx_15_8_pipeline_run_lifecycle.sql").read_text()
    full = (ROOT / "SUPABASE_FULL_MIGRATION.sql").read_text()
    assert "pipeline_runs_status_check" in migration
    assert "CHECK (status IN ('queued','running','completed','failed','partial'))" in migration
    assert "CANONICAL MIGRATION 23: 20261001000700_leadx_15_8_pipeline_run_lifecycle.sql" in full


def test_frontend_status_polling_surfaces_api_errors():
    text = (ROOT / "lead-crm-portal/app/(admin)/admin/pipeline/page.tsx").read_text()
    assert "pollError" in text
    assert "Unable to read live run status" in text


def test_required_route_contract_is_fail_closed():
    text = (ROOT / "app/app_service.py").read_text()
    for route in (
        "/auth/email/send-code",
        "/billing/create-checkout-session",
        "/crm/{tenant_id}/agent-missions",
        "/crm/{tenant_id}/agents",
        "/crm/{tenant_id}/ai/brief",
        "/crm/{tenant_id}/companies",
        "/crm/{tenant_id}/contacts",
        "/crm/{tenant_id}/deals",
        "/crm/{tenant_id}/fabric/overview",
        "/support/ask",
    ):
        assert route in text
    assert "Required LeadX routes failed to mount" in text


def test_c_tier_and_custom_email_prompt_contracts():
    ui = (ROOT / "lead-crm-portal/app/(admin)/admin/pipeline/page.tsx").read_text()
    api = (ROOT / "app/crm/crm_api.py").read_text()
    llm = (ROOT / "app/integrations/prod/llm_copy.py").read_text()
    assert 'aria-label="Allow Tier C"' in ui
    assert 'allow_tier_c: form.allow_tier_c' in ui
    assert 'prompt: str = ""' in api
    assert 'custom_prompt=body.prompt' in api
    assert 'custom_prompt: str = ""' in llm


def test_small_target_discovery_can_continue_past_first_batch():
    from app.integrations.prod.pipeline import _max_rounds_for_target
    assert _max_rounds_for_target(5) > 5


def test_decodo_fallback_and_pipeline_persistence_retry_contracts():
    discovery = (ROOT / "app/integrations/prod/multi_source_discovery.py").read_text()
    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text()
    assert "DECODO_API_KEY" in discovery
    assert "_web_search" in discovery
    assert "async def _pipeline_db" in pipeline
