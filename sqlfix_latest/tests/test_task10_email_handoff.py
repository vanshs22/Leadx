"""Task 10 deterministic email handoff checks.

The sink below is an in-process provider contract fixture. It never opens a
socket, reads credentials, sends mail, or retains message bodies after purge.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

from conftest import FakeSupabase


@dataclass
class LocalMailSink:
    """Deterministic acceptance/failure sink for the SES send contract."""

    fail: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0

    def send(self, **message: Any) -> str:
        self.attempts += 1
        if self.fail:
            raise RuntimeError("local_sink_rejected")
        message_id = f"local-{self.attempts}"
        self.messages.append({"id": message_id, **message})
        return message_id

    def evidence(self) -> dict[str, Any]:
        return {
            "acceptance": "accepted" if self.messages else "none",
            "message_id_hashes": [
                hashlib.sha256(str(row["id"]).encode()).hexdigest()
                for row in self.messages
            ],
            "outbound_attempts": self.attempts,
            "retained_message_bodies": 0,
        }

    def purge(self) -> None:
        self.messages.clear()


class _SesClient:
    def __init__(self, sink: LocalMailSink):
        self.sink = sink

    def send_email(self, **kwargs: Any) -> dict[str, str]:
        return {"MessageId": self.sink.send(**kwargs)}


def _ready_ses(monkeypatch, sink: LocalMailSink) -> None:
    from app.integrations.prod import ses_domain

    monkeypatch.setattr(ses_domain, "ses_status", lambda: {"ready": True, "ok": True})
    monkeypatch.setattr(ses_domain, "ses_configured", lambda: True)
    monkeypatch.setattr(ses_domain, "_client", lambda: _SesClient(sink))


def test_ses_identity_contract_normalizes_domain_and_returns_dns_records(monkeypatch):
    from app.integrations.prod import ses_domain

    class IdentityClient:
        def create_email_identity(self, **_kwargs):
            return {}

        def get_email_identity(self, **kwargs):
            assert kwargs == {"EmailIdentity": "client.example.test"}
            return {
                "VerifiedForSendingStatus": False,
                "DkimAttributes": {"Status": "PENDING", "Tokens": ["token-1", "token-2"]},
            }

    monkeypatch.setattr(ses_domain, "ses_status", lambda: {"ready": True, "ok": True})
    monkeypatch.setattr(ses_domain, "_client", IdentityClient)

    result = ses_domain.create_domain_identity(" HTTPS://Client.Example.TEST/path ")

    assert result["ok"] is True
    assert result["domain"] == "client.example.test"
    assert result["verified"] is False
    assert result["dns_records"] == [
        {
            "type": "CNAME",
            "name": "token-1._domainkey.client.example.test",
            "value": "token-1.dkim.amazonses.com",
        },
        {
            "type": "CNAME",
            "name": "token-2._domainkey.client.example.test",
            "value": "token-2.dkim.amazonses.com",
        },
    ]


def test_send_to_lead_hands_off_normalized_message_to_local_sink(monkeypatch):
    from app.integrations.prod import discovery_runtime, email_outreach, send_policy

    tenant = "tenant-sink"
    fake = FakeSupabase({
        "lead_assignments": [{"id": "assignment-1", "tenant_id": tenant, "lead_id": "lead-1"}],
        "leads_global": [{
            "id": "lead-1",
            "email": "  recipient@example.test  ",
            "name": "Synthetic Gym",
            "email_valid": True,
            "state": "NJ",
        }],
        "email_optouts": [],
        "tenant_email_settings": [{
            "tenant_id": tenant,
            "domain": " Client.Example.TEST ",
            "from_email": " Sales@Client.Example.TEST ",
            "from_name": "Client",
            "domain_verified": True,
        }],
    })
    sink = LocalMailSink()
    _ready_ses(monkeypatch, sink)
    monkeypatch.setattr(email_outreach, "_sb", lambda: fake)
    monkeypatch.setattr(email_outreach, "is_email_opted_out", lambda *_args: asyncio.sleep(0, result=False))
    monkeypatch.setattr(email_outreach, "email_deliverable", lambda _email: asyncio.sleep(0, result=True))
    from app.integrations.prod import unsubscribe
    monkeypatch.setattr(unsubscribe, "unsubscribe_url", lambda _tenant, _email: "https://local.test/u")
    monkeypatch.setattr(discovery_runtime, "assert_provider_allowed", lambda _provider: None)
    monkeypatch.setattr(send_policy, "can_send_now", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(send_policy, "reserve_domain_slot", lambda *_args: True)
    monkeypatch.setattr(send_policy, "record_touch", lambda *_args: None)
    # Sends are blocked without a sender profile (CAN-SPAM); supply it via the platform env fallback.
    monkeypatch.setenv("SENDER_NAME", "Client")
    monkeypatch.setenv("SENDER_POSTAL_ADDRESS", "1 Main St, Hoboken NJ")
    monkeypatch.setenv("PRODUCT_DESCRIPTION", "Synthetic product")

    try:
        result = asyncio.run(email_outreach.send_to_lead(
            tenant,
            "assignment-1",
            "Hello {{name}}",
            "<p>Welcome {{name}}</p>",
            "Welcome {{name}}",
            provider_operation_id="handoff-1",
        ))

        assert result["ok"] is True
        assert result["to"] == "recipient@example.test"
        assert result["subject"] == "Hello Synthetic Gym"
        assert len(sink.messages) == 1
        message = sink.messages[0]
        assert message["FromEmailAddress"] == "Client <Sales@Client.Example.TEST>"
        assert message["Destination"] == {"ToAddresses": ["recipient@example.test"]}
        assert message["Content"]["Simple"]["Subject"]["Data"] == "Hello Synthetic Gym"
        assert "recipient@example.test" not in str(sink.evidence())
        assert "Welcome Synthetic Gym" not in str(sink.evidence())
        assert sink.evidence()["outbound_attempts"] == 1
    finally:
        sink.purge()

    assert sink.messages == []
    assert sink.evidence()["retained_message_bodies"] == 0


def test_local_sink_failure_is_safe_and_does_not_claim_acceptance(monkeypatch):
    from app.integrations.prod import ses_domain

    sink = LocalMailSink(fail=True)
    _ready_ses(monkeypatch, sink)

    result = asyncio.run(ses_domain.send_via_ses(
        "recipient@example.test",
        "Synthetic subject",
        "synthetic body",
        from_addr="sales@client.example.test",
    ))

    assert result["ok"] is False
    assert result["provider"] == "ses"
    assert result["error"] == "local_sink_rejected"
    assert sink.evidence()["acceptance"] == "none"
    assert sink.evidence()["outbound_attempts"] == 1
    assert "synthetic body" not in str(sink.evidence())
    sink.purge()
    assert sink.messages == []


def test_sequence_step_record_is_idempotent_for_same_enrollment_and_step(monkeypatch):
    from app.integrations.prod import sequences

    fake = FakeSupabase({
        "email_sequence_sends": [{
            "id": "send-1",
            "enrollment_id": "enrollment-1",
            "step_number": 1,
            "status": "sending",
            "provider_id": None,
        }],
    })
    monkeypatch.setattr(sequences, "_sb", lambda: fake)

    enrollment = {
        "id": "enrollment-1",
        "tenant_id": "tenant-sink",
        "assignment_id": "assignment-1",
    }
    first = sequences._record_step_start(enrollment, 1, "recipient@example.test", "Hello")
    replay = sequences._record_step_start(enrollment, 1, "recipient@example.test", "Hello")

    assert first is None
    assert replay is None
    assert len(fake.rows("email_sequence_sends")) == 1
