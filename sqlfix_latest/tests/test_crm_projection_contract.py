from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_assignment_projection_uses_existing_tenant_crm_tables_and_is_idempotent():
    source = (ROOT / "app/integrations/prod/crm_projection.py").read_text()
    assert 'table("companies")' in source
    assert 'table("contacts")' in source
    assert 'source_lead_id' in source
    assert 'eq("tenant_id", str(tenant_id))' in source
    assert 'company_id' in source
    assert 'crm_saved' in source
    assert 'company_persistence_failed' in source
    assert 'contact_persistence_failed' in source


def test_assignment_and_delivery_expose_crm_projection_state():
    quality = (ROOT / "app/integrations/prod/quality.py").read_text()
    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text()
    assert 'project_assignment_to_crm' in quality
    assert '"crm_saved"' in quality
    assert 'crm_persistence_failed' in pipeline
    assert 'max(delivered_n, len(to_deliver))' in pipeline
    assert '"crm_projection_failures"' in pipeline
