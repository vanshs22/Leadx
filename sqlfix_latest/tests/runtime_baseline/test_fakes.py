"""Tests for the offline baseline dependency harness.

These tests validate the harness itself. They do not alter or assert a fixed
production contract; the archived behavior observations are captured by the
separate baseline report and remain isolated from the normal regression suite.
"""
from .fakes import (
    FakeProvider,
    FakeQueueStore,
    FakeRedis,
    PollingProbe,
    observe_unfixed_claim_next,
)


def test_queue_fake_distinguishes_inputs_before_legacy_observation():
    empty = FakeQueueStore()
    outage = FakeQueueStore(fail_claim=True)

    assert observe_unfixed_claim_next(empty, "worker-test") is None
    assert observe_unfixed_claim_next(outage, "worker-test") is None
    assert empty.calls and outage.calls


def test_redis_fake_is_offline_and_recoverable():
    redis = FakeRedis(available=False)
    try:
        redis.get("rl:api:synthetic-tenant")
    except ConnectionError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unavailable fake Redis unexpectedly returned")

    redis.available = True
    assert redis.setex("cache:synthetic-tenant", 5, "snapshot")
    assert redis.get("cache:synthetic-tenant") == "snapshot"


def test_provider_fake_records_identity_without_provider_calls():
    provider = FakeProvider()
    first = provider.execute(operation_id="job-synthetic-1", payload={"tenant": "tenant-a"})
    second = provider.execute(operation_id="job-synthetic-1", payload={"tenant": "tenant-a"})

    assert first["operation_id"] == second["operation_id"]
    assert [call["operation_id"] for call in provider.calls] == ["job-synthetic-1", "job-synthetic-1"]


def test_polling_probe_is_deterministic():
    probe = PollingProbe(["loading", "pending", "error"])
    assert [probe.next(), probe.next(), probe.next()] == ["loading", "pending", "error"]
    assert probe.attempts == 3
