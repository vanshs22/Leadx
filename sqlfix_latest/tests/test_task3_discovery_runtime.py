"""Task 3 generated-style properties for local-first discovery accounting."""
from __future__ import annotations

import asyncio

from conftest import FakeSupabase


def test_property_3_free_first_paid_gates_run_for_128_cases(monkeypatch):
    from app.integrations.prod.paid_policy import paid_stage_allowed

    monkeypatch.setenv("LEADX_PAID_FALLBACK", "auto")
    for case in range(128):
        want = 1 + (case % 7)
        free_count = case % (want + 1)
        rows = [{"website": f"https://free-{case}-{i}.test"} for i in range(free_count)]
        # A closed qualifying gap always blocks paid work.
        allowed, reason = paid_stage_allowed(
            rows, want, credential_available=True,
            unmet_gap=free_count < want, remaining_quota=want,
        )
        assert allowed is (free_count < want)
        if not allowed:
            assert reason == "free_sources_sufficient"
        # Local Validation Mode is unconditional, even with every other gate.
        blocked, local_reason = paid_stage_allowed(
            rows, want, credential_available=True, unmet_gap=True,
            remaining_quota=want, local_mode=True,
        )
        assert blocked is False and local_reason == "local_validation_mode"


def test_property_4_progress_never_counts_non_persisted_provider_work():
    from app.integrations.prod.discovery_runtime import project_progress

    for case in range(128):
        total = 1 + case % 9
        tasks = [{"id": f"task-{case}-{i}", "status": "running"} for i in range(total)]
        persisted = case % (total + 1)
        results = [
            {"task_id": f"task-{case}-{i}", "status": "completed", "persisted": True,
             "qualifying": i % 2 == 0, "billable": i % 3 == 0}
            for i in range(persisted)
        ]
        projection = project_progress(tasks, results, total)
        assert projection["completed"] == persisted
        assert projection["remaining"] == total - persisted
        assert projection["billable"] == sum(i % 3 == 0 for i in range(persisted))
        # Provider work that has not reached durable result storage is not done.
        assert project_progress(tasks, [], total)["completed"] == 0


def test_property_5_quota_settlement_is_exactly_once_for_128_terminal_cases():
    from app.integrations.prod.discovery_runtime import settle_reservation

    for case in range(128):
        reservation_id = f"reservation-{case}"
        tenant_id = f"tenant-{case}"
        reserved = 1 + case % 8
        committed = case % (reserved + 1)
        fake = FakeSupabase({"quota_reservations": [{
            "id": reservation_id, "tenant_id": tenant_id, "status": "reserved",
            "reserved_quantity": reserved, "committed_quantity": 0, "released_quantity": 0,
        }]})
        first = settle_reservation(
            fake, reservation_id=reservation_id, tenant_id=tenant_id,
            settlement_key=f"settlement-{case}", billable=committed, reserved=reserved,
        )
        second = settle_reservation(
            fake, reservation_id=reservation_id, tenant_id=tenant_id,
            settlement_key=f"settlement-{case}", billable=committed, reserved=reserved,
        )
        row = fake.rows("quota_reservations")[0]
        assert first.ok and second.ok and second.replayed
        assert row["committed_quantity"] == committed
        assert row["released_quantity"] == reserved - committed
        assert len(fake.rows("quota_settlements")) == 1


def test_local_discovery_and_verification_are_deterministic_and_network_free(monkeypatch):
    monkeypatch.setenv("LOCAL_VALIDATION_MODE", "true")
    from app.integrations.prod.discovery import discover_leads
    from app.integrations.prod.contact_verification_mesh import verify_email, verify_phone

    async def run():
        rows = await discover_leads("dental", ["Synthetic City"], limit_per_location=3)
        email = await verify_email("fixture@example.test")
        phone = await verify_phone("+1 908 642 3187")
        return rows, email, phone

    rows, email, phone = asyncio.run(run())
    assert len(rows) == 3 and all(r["source"] == "local_fixture" and r["billable"] is False for r in rows)
    assert email["observations"][0]["provider"] == "local_fixture"
    assert phone["observations"][0]["provider"] == "local_fixture"
