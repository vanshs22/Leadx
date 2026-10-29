"""Focused contracts for operator stop and client request withdrawal controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_stop_uses_durable_scoped_cancellation():
    ops = (ROOT / "app/api/prod/ops_api.py").read_text(encoding="utf-8")
    jobs = (ROOT / "app/integrations/prod/jobs.py").read_text(encoding="utf-8")
    portal = (ROOT / "lead-crm-portal/app/(admin)/admin/runs/[runId]/page.tsx").read_text(encoding="utf-8")
    assert '@router.post("/admin/runs/{run_id}/stop")' in ops
    assert 'operation": "cancel"' in ops
    assert "jobs.cancel_job(job_id" in ops
    assert 'adminExtras.stopRun(runId)' in portal
    assert '"Stop run"' in portal
    assert 'status": "cancelled"' in jobs


def test_client_withdraw_is_tenant_scoped_and_fences_late_finalization():
    discovery = (ROOT / "app/integrations/prod/discovery_requests.py").read_text(encoding="utf-8")
    crm = (ROOT / "app/crm/crm_api.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text(encoding="utf-8")
    search = (ROOT / "lead-crm-portal/app/(portal)/search/page.tsx").read_text(encoding="utf-8")
    growth = (ROOT / "lead-crm-portal/app/(portal)/growth/page.tsx").read_text(encoding="utf-8")
    assert 'def withdraw_request(' in discovery
    assert 'status": "cancelled"' in discovery
    assert 'settlement_key=f"discovery:{request_id}:withdrawal"' in discovery
    assert 'eq("tenant_id", str(tenant_id))' in discovery
    assert '/{tenant_id}/discovery-requests/{request_id}/withdraw' in crm
    assert 'in_("status", ["approved", "queued", "running"])' in pipeline
    assert "delivery_ready_n" in pipeline
    assert 'delivered_n >= slots_cap' in pipeline
    assert "dispatch_post_delivery_agents" in pipeline
    assert "POST_DELIVERY_AGENTS" in (ROOT / "app/integrations/prod/post_run_agent_dispatch.py").read_text(encoding="utf-8")
    assert '"reason": "crm_persistence_failed"' in (ROOT / "app/integrations/prod/quality.py").read_text(encoding="utf-8")
    assert 'withdrawDiscoveryRequest' in search and 'Withdraw' in search
    assert 'withdrawDiscoveryRequest' in growth and 'Withdraw' in growth
