"""Task 4: deterministic replay checks at the disposable local boundary.

The replay authority is entirely in-memory and run-tagged. These checks do
not read configuration, contact providers, create remote work, or use a real
tenant.

**Validates: Requirements 2.2, 3.4**
"""
from __future__ import annotations

import pytest

from tests.fixtures.disposable_validation import (
    DurableReplayStore,
    FixtureBoundaryError,
    FixtureRequest,
    InMemoryFixtureStore,
    provision_fixture,
)


def _request(run_tag: str = "test-replay-001", **overrides) -> FixtureRequest:
    values = {
        "profile": "disposable-local-test",
        "run_tag": run_tag,
        "tenant_id": f"synthetic-tenant-{run_tag}",
        "tenant_tag": f"synthetic-tenant-{run_tag}",
        "icp_id": f"synthetic-icp-{run_tag}",
        "icp_tag": f"synthetic-icp-{run_tag}",
        "cleanup_proven": True,
        "guards_restored": True,
        "target": 5,
    }
    values.update(overrides)
    return FixtureRequest(**values)


@pytest.mark.parametrize("terminalizer", ("complete", "fence"))
def test_first_replay_conflict_and_fresh_key_are_deterministic(terminalizer: str):
    """One key owns one identity; conflicts stay opaque and fresh work is distinct."""

    fixture_store = InMemoryFixtureStore()
    fixture = provision_fixture(_request(f"test-replay-{terminalizer}"), store=fixture_store)
    fixture.create_pipeline(target=5)
    replay = DurableReplayStore(fixture_store)
    payload = {"vertical": "synthetic", "location": "local", "target": 5}

    first = replay.submit(
        fixture,
        idempotency_key="request-a",
        tenant_id=fixture.tenant_id,
        payload=payload,
        target=5,
    )
    same_key = replay.submit(
        fixture,
        idempotency_key="request-a",
        tenant_id=fixture.tenant_id,
        payload=payload,
        target=5,
    )
    conflicting_payload = replay.submit(
        fixture,
        idempotency_key="request-a",
        tenant_id=fixture.tenant_id,
        payload={**payload, "location": "another-local-value"},
        target=5,
    )
    conflicting_tenant = replay.submit(
        fixture,
        idempotency_key="request-a",
        tenant_id="synthetic-tenant-foreign",
        payload=payload,
        target=5,
    )

    assert first == same_key
    assert first["status"] == "active"
    assert "identity" in first
    assert conflicting_payload == {
        "status": "conflict",
        "reason_code": "idempotency_mismatch",
    }
    assert conflicting_tenant == conflicting_payload
    assert first["identity"] not in str(conflicting_payload)
    assert fixture.tenant_id not in str(conflicting_payload)
    assert replay.active_work_count == 1
    assert replay.provider_executions == 1
    assert len(replay.records) == 1

    getattr(replay, terminalizer)("request-a")
    fresh = replay.submit(
        fixture,
        idempotency_key="request-b",
        tenant_id=fixture.tenant_id,
        payload=payload,
        target=5,
    )

    assert fresh["status"] == "active"
    assert fresh["identity"] != first["identity"]
    assert len({record.identity for record in replay.records.values()}) == 2
    assert replay.active_work_count == 1
    assert replay.provider_executions == 2
    assert replay.records["request-a"].status == {"complete": "terminal", "fence": "fenced"}[terminalizer]

    fixture.add_artifact(f"replay-evidence-{fixture.run_tag}")
    evidence = replay.sanitized_evidence(fixture)
    assert len(evidence) == 2
    assert all(
        set(item) == {"run_tag", "identity_fingerprint", "status", "active", "evidence_class"}
        and item["run_tag"] == fixture.run_tag
        and item["evidence_class"] == "local"
        for item in evidence
    )
    assert all("request-a" not in str(item) and "vertical" not in str(item) for item in evidence)

    cleanup = fixture.cleanup()
    assert cleanup["status"] == "PASS"
    assert cleanup["run_tag"] == fixture.run_tag
    assert cleanup["replay_records_removed"] == 2
    assert fixture_store.rows == []
    assert fixture_store.artifacts == []
    assert fixture_store.replay_records == {}
    assert fixture_store.provider_execution_ids == set()


def test_tenant_and_cap_guards_reject_before_replay_work():
    """Tenant mismatch and targets above 500 cannot create durable identities."""

    fixture_store = InMemoryFixtureStore()
    fixture = provision_fixture(_request("test-replay-guards"), store=fixture_store)
    replay = DurableReplayStore(fixture_store)

    wrong_tenant = replay.submit(
        fixture,
        idempotency_key="foreign-request",
        tenant_id="synthetic-tenant-foreign",
        payload={"vertical": "synthetic"},
        target=5,
    )
    assert wrong_tenant == {"status": "rejected", "reason_code": "tenant_mismatch"}
    assert replay.records == {}
    assert replay.provider_executions == 0

    with pytest.raises(FixtureBoundaryError) as raised:
        replay.submit(
            fixture,
            idempotency_key="over-cap",
            tenant_id=fixture.tenant_id,
            payload={"vertical": "synthetic"},
            target=501,
        )
    assert raised.value.reasons == ("cap-above-500",)
    assert replay.records == {}
    assert replay.active_work_count == 0
    assert replay.provider_executions == 0
