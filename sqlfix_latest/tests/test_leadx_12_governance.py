import pytest
from app.integrations.prod.tenant_scope import tenant_insert, tenant_table

def test_tenant_insert_forces_scope():
    assert tenant_insert("x","tenant-a",{"name":"a"})["tenant_id"]=="tenant-a"
    with pytest.raises(ValueError): tenant_insert("x","tenant-a",{"tenant_id":"tenant-b"})

class _Filter:
    def __init__(self, log): self.log = log
    def eq(self, k, v): self.log.append(("eq", k, v)); return self
    def limit(self, n): return self

class _RequestBuilder:  # mirrors supabase-py: NO .eq() on the table builder
    def __init__(self, log): self.log = log
    def select(self, *a, **k): self.log.append(("select",)); return _Filter(self.log)
    def update(self, v, *a, **k): self.log.append(("update", v)); return _Filter(self.log)
    def delete(self, *a, **k): self.log.append(("delete",)); return _Filter(self.log)
    def insert(self, v, *a, **k): self.log.append(("insert", v)); return self

class _SB:
    def __init__(self): self.log = []
    def table(self, name): return _RequestBuilder(self.log)

def test_tenant_query_always_contains_tenant_filter():
    sb = _SB()
    tenant_table(sb, "leads_global", "tenant-a").select("*").limit(1)
    tenant_table(sb, "leads_global", "tenant-a").update({"x": 1}).eq("id", "1")
    tenant_table(sb, "leads_global", "tenant-a").delete()
    assert sb.log.count(("eq", "tenant_id", "tenant-a")) == 3
    tenant_table(sb, "leads_global", "tenant-a").insert({"name": "a"})
    assert ("insert", {"name": "a", "tenant_id": "tenant-a"}) in sb.log
    with pytest.raises(ValueError): tenant_table(sb, "t", "tenant-a").insert({"tenant_id": "tenant-b"})
    with pytest.raises(ValueError): tenant_table(sb, "t", "")

def test_approval_settings_route_registered_before_item_route():
    text = open("app/api/prod/approval_api.py").read()
    assert text.index("/approvals/settings") < text.index("/approvals/{item_id}")

def test_contract_has_one_registry_and_governance():
    from app.integrations.prod.agent_registry import AGENT_REGISTRY
    assert {"prospecting","enrichment","research","signal","qualification","outreach","crm","deal","revenue","learning"} <= set(AGENT_REGISTRY)
    assert all("allowed_tools" in c and "input_schema" in c and "output_schema" in c and "needs_approval" in c and "max_cost" in c and "max_duration_seconds" in c for c in AGENT_REGISTRY.values())

def test_notify_owner_import_is_from_unsubscribe():
    text=open("app/integrations/prod/automation_engine.py").read()
    assert "from app.integrations.prod.unsubscribe import notify_tenant" in text

def test_generic_connector_guards_customer_url():
    text=open("app/integrations/prod/outbound_sync.py").read()
    assert "assert_public_url(url)" in text
    connector=open("app/integrations/prod/connectors.py").read()
    assert 'provider in ("webhook", "zapier", "make", "n8n", "generic_http")' in connector
    assert "assert_public_url(url)" in connector

def test_ssrf_guard_rejects_private_customer_url():
    from app.integrations.prod.net_safety import is_public_url
    ok, reason = is_public_url("http://127.0.0.1:8080/test", resolve=False)
    assert not ok and reason == "private_address"
