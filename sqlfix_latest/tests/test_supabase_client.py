"""Focused Supabase client header contracts."""
from __future__ import annotations

import pytest

from app.memory.supabase_client import get_supabase, reset_supabase_client


@pytest.fixture(autouse=True)
def _reset_client():
    reset_supabase_client()
    yield
    reset_supabase_client()


def _client(monkeypatch, key: str):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", key)
    return get_supabase()


def _lower_headers(headers):
    return {str(name).lower(): value for name, value in headers.items()}


def test_modern_server_secret_uses_placeholder_then_real_apikey(monkeypatch):
    import supabase

    key = "sb_secret_synthetic-test-key"
    captured = {}
    real_create_client = supabase.create_client

    def recording_create_client(url, factory_key, *, options=None):
        captured["factory_key"] = factory_key
        return real_create_client(url, factory_key, options=options)

    monkeypatch.setattr(supabase, "create_client", recording_create_client)
    client = _client(monkeypatch, key)

    placeholder = captured["factory_key"]
    assert placeholder != key
    assert placeholder.count(".") == 2
    assert client.supabase_key == key

    for headers in (
        client.options.headers,
        client.auth._headers,
        client.auth.admin._headers,
        client.postgrest.session.headers,
    ):
        normalized = _lower_headers(headers)
        assert normalized["apikey"] == key
        assert "authorization" not in normalized
        assert placeholder not in normalized.values()


def test_legacy_key_keeps_supabase_py_auth_headers(monkeypatch):
    key = "eyJhbGciOiJIUzI1NiJ9.synthetic.jwt"

    client = _client(monkeypatch, key)
    headers = _lower_headers(client.postgrest.session.headers)

    assert headers["apikey"] == key
    assert headers["authorization"] == f"Bearer {key}"

    for auth_headers in (client.auth._headers, client.auth.admin._headers):
        normalized = _lower_headers(auth_headers)
        assert normalized["apikey"] == key
        assert normalized["authorization"] == f"Bearer {key}"
