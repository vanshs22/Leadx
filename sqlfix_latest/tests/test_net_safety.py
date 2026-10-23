"""SSRF guard + webhook secret tests (pure stdlib, no network)."""
import os
import pytest

from app.integrations.prod import net_safety as ns


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8000/x", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/", "http://192.168.1.10/", "http://172.16.0.1/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://2130706433/", "http://0x7f000001/",
    "http://100.64.0.1/", "ftp://example.com/", "file:///etc/passwd", "http://user:pw@example.com/",
    "http://svc.internal/", "http://printer.local/", "", "http:///nohost",
])
def test_blocks_unsafe(url):
    ok, _ = ns.is_public_url(url, resolve=False)
    assert not ok


def test_allows_public_ip_and_host_without_dns():
    assert ns.is_public_url("https://8.8.8.8/", resolve=False)[0]
    assert ns.is_public_url("https://www.example.com/x", resolve=False)[0]


def test_dev_override_ignored_in_production(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert not ns.is_public_url("http://127.0.0.1/", resolve=False)[0]
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert ns.is_public_url("http://127.0.0.1/", resolve=False)[0]


def test_sns_subscribe_url():
    assert ns.is_valid_sns_subscribe_url("https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription")
    assert not ns.is_valid_sns_subscribe_url("http://sns.us-east-1.amazonaws.com/")
    assert not ns.is_valid_sns_subscribe_url("https://sns.us-east-1.amazonaws.com.evil.io/")
    assert not ns.is_valid_sns_subscribe_url("https://169.254.169.254/")


def test_webhook_secret_fail_closed_in_prod(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert ns.webhook_secret_ok(None)[0] is False
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret-value-123")
    assert ns.webhook_secret_ok("s3cret-value-123")[0] is True
    assert ns.webhook_secret_ok("wrong")[0] is False
    monkeypatch.delenv("WEBHOOK_SECRET")
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert ns.webhook_secret_ok(None)[0] is True
