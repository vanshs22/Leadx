from __future__ import annotations

import asyncio

import httpx

from app.integrations.prod import crm_delivery


class _Response:
    def __init__(self, status_code=201, body=None, text=""):
        self.status_code = status_code
        self.headers = {}
        self.content = text.encode()
        self.text = text
        self._body = body or {"id": "external-1"}

    def json(self):
        return self._body


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return self.response


def _request(provider):
    return crm_delivery.make_request(
        tenant_id="tenant-1", assignment_id="assignment-1", lead_id="lead-1",
        provider=provider, stable_event_id=f"assignment:assignment-1:delivery:{provider}",
        lead={"name": "Example Co", "email": "owner@example.test", "phone": "+15555550100"},
        mapping={"email": "email", "name": "name"},
    )


def test_registry_covers_all_supported_crms():
    assert set(crm_delivery.CRM_PROVIDERS) == set(crm_delivery.ADAPTER_REGISTRY)


def test_operation_id_is_stable_and_is_sent_to_provider():
    first = _request("hubspot")
    second = _request("hubspot")
    assert first.provider_operation_id == second.provider_operation_id

    client = _Client()
    result = asyncio.run(crm_delivery.execute_crm_delivery(
        first, {"access_token": "test-token"}, client=client,
    ))

    assert result.ok is True
    assert result.provider_operation_id == first.provider_operation_id
    assert client.calls[0][2]["headers"]["X-LeadX-Idempotency-Key"] == first.provider_operation_id
    assert "test-token" not in str(result.as_dict())


def test_each_adapter_uses_mocked_http_only():
    configs = {
        "salesforce": {"instance_url": "https://example.salesforce.test", "access_token": "test-token"},
        "hubspot": {"access_token": "test-token"},
        "pipedrive": {"api_key": "test-key"},
        "close": {"api_key": "test-key"},
        "gohighlevel": {"access_token": "test-token", "location_id": "location-1"},
    }
    for provider, config in configs.items():
        client = _Client()
        result = asyncio.run(crm_delivery.execute_crm_delivery(_request(provider), config, client=client))
        assert result.ok is True, provider
        assert client.calls and client.calls[0][0] == "POST", provider
        assert client.calls[0][2]["headers"]["X-LeadX-Operation-Id"]


def test_unconfigured_and_disabled_never_call_http():
    client = _Client()
    request = _request("close")
    missing = asyncio.run(crm_delivery.execute_crm_delivery(request, {}, client=client))
    disabled = asyncio.run(crm_delivery.execute_crm_delivery(request, {"enabled": False}, client=client))
    assert missing.safe_error_code == "connector_unconfigured"
    assert disabled.safe_error_code == "connector_disabled"
    assert client.calls == []


def test_retry_timeout_is_ambiguous_and_error_is_safe():
    client = _Client(error=httpx.TimeoutException("Bearer secret-token timed out"))
    result = asyncio.run(crm_delivery.execute_crm_delivery(
        _request("salesforce"), {"instance_url": "https://example.salesforce.test", "access_token": "secret-token"}, client=client,
    ))
    assert result.retryable is True
    assert result.ambiguous is True
    assert result.safe_error_code == "ambiguous_timeout"
    assert "secret-token" not in str(result.as_dict())


def test_http_classification_handles_rate_limit_without_provider_body():
    outcome = crm_delivery.classify_http(429, '{"access_token":"secret-token"}', {"Retry-After": "7"})
    assert outcome["retryable"] is True
    assert outcome["safe_error_code"] == "rate_limited"
    assert outcome["retry_after_seconds"] == 7
    assert "secret-token" not in outcome["safe_error_detail"]


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.data = []

    def update(self, patch):
        self.db.updates.append((self.name, patch))
        return self

    def eq(self, *_args):
        return self

    def upsert(self, row, **_kwargs):
        self.db.upserts.append((self.name, row))
        return self

    def execute(self):
        return type("R", (), {"data": self.data})()


class _DB:
    def __init__(self):
        self.updates = []
        self.upserts = []

    def table(self, name):
        return _Query(self, name)


def test_outbox_timeout_records_retry_attempt_and_reconciliation(monkeypatch):
    db = _DB()
    monkeypatch.setattr(crm_delivery, "resolve_tenant_connector", lambda *_args, **_kwargs: {
        "state": "ready", "config": {"access_token": "test-token"},
    })
    from app.integrations.prod import data_plane
    monkeypatch.setattr(data_plane, "get_supabase", lambda: db)
    row = {
        "id": "outbox-1", "tenant_id": "tenant-1", "aggregate_id": "assignment-1",
        "stable_event_id": "assignment:assignment-1:delivery:hubspot", "destination": "hubspot",
        "provider": "hubspot", "attempts": 0, "payload_hash": "hash-1",
        "payload": {"assignment_id": "assignment-1", "lead_id": "lead-1", "lead": {"name": "Example Co"}},
    }
    result = asyncio.run(data_plane.process_delivery_outbox_row(
        row, client=_Client(error=httpx.TimeoutException("timed out")),
    ))
    assert result["safe_error_code"] == "ambiguous_timeout"
    assert any(name == "delivery_outbox_attempts" for name, _ in db.upserts)
    assert any(name == "delivery_reconciliations" for name, _ in db.upserts)
    patch = next(patch for name, patch in db.updates if name == "delivery_outbox")
    assert patch["outbox_state"] == "retryable"
    assert patch["safe_error_code"] == "ambiguous_timeout"


def test_connector_resolution_distinguishes_disabled_and_unconfigured(monkeypatch):
    from app.integrations.prod import connectors

    monkeypatch.setattr(connectors, "resolve_connection", lambda *_args, **_kwargs: {"id": "c-1", "status": "disabled"})
    disabled = crm_delivery.resolve_tenant_connector("tenant-1", "hubspot")
    assert disabled["state"] == "disabled"

    monkeypatch.setattr(connectors, "resolve_connection", lambda *_args, **_kwargs: None)
    missing = crm_delivery.resolve_tenant_connector("tenant-1", "hubspot")
    assert missing["state"] == "unconfigured"
