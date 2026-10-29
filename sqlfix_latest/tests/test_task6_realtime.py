"""Task 6 local-only properties for tenant-safe CRM invalidation.

These tests deliberately use a synthetic transport and authoritative API
projection. They never contact Supabase, providers, or the CRM backend.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261020001600_leadx_21_realtime_capture.sql"
FULL = ROOT / "SUPABASE_FULL_MIGRATION.sql"
PORTAL = ROOT / "lead-crm-portal"


@dataclass(frozen=True)
class Invalidation:
    tenant_id: str
    table: str
    event: str


@dataclass
class SyntheticRealtime:
    members: dict[str, set[str]]
    subscriptions: dict[str, set[str]] = field(default_factory=dict)
    deliveries: dict[str, list[Invalidation]] = field(default_factory=dict)
    projection: dict[str, int] = field(default_factory=dict)
    refetches: dict[str, int] = field(default_factory=dict)

    def subscribe(self, user: str, tenant_id: str) -> None:
        if user not in self.members.get(tenant_id, set()):
            raise PermissionError("private tenant topic denied")
        self.subscriptions.setdefault(tenant_id, set()).add(user)
        self.deliveries.setdefault(user, [])

    def unsubscribe(self, user: str, tenant_id: str) -> None:
        self.subscriptions.get(tenant_id, set()).discard(user)

    def mutate(self, tenant_id: str, table: str, version: int) -> Invalidation:
        self.projection[tenant_id] = max(version, self.projection.get(tenant_id, 0))
        event = Invalidation(tenant_id, table, "UPDATE")
        for user in self.subscriptions.get(tenant_id, set()):
            self.deliveries.setdefault(user, []).append(event)
        return event

    def authoritative_refetch(self, user: str, tenant_id: str) -> int:
        if user not in self.members.get(tenant_id, set()):
            raise PermissionError("authoritative API denied")
        self.refetches[user] = self.refetches.get(user, 0) + 1
        return self.projection.get(tenant_id, 0)


def test_task6_migration_has_event_trigger_and_full_install_parity():
    migration = MIGRATION.read_text(encoding="utf-8").lower()
    full = FULL.read_text(encoding="utf-8")
    assert "create trigger leadx_event_broadcast" in migration
    assert "after insert on public.leadx_event_log" in migration
    assert "alter publication supabase_realtime add table public.%i" in migration
    assert "realtime.topic() ~* '^crm:[0-9a-f]{8}-" in migration
    assert "no admin aggregate" in migration
    assert "leadx_event_broadcast" in full


def test_task6_property_private_reconciliation_for_100_generated_sequences():
    """Property 9: duplicate/reordered/missed hints converge by API refetch."""
    rng = random.Random(20261006)
    for iteration in range(100):
        harness = SyntheticRealtime({"tenant-a": {"user-a"}, "tenant-b": {"user-a", "user-b"}})
        harness.subscribe("user-a", "tenant-a")
        harness.subscribe("user-b", "tenant-b")

        count = 2 + rng.randrange(8)
        events = [harness.mutate("tenant-a", "deals", version) for version in range(1, count + 1)]
        # Delivery may duplicate, reorder, or miss any hint. The payload remains
        # metadata-only; the newest projection is never taken from the event.
        delivered = [event for event in events if rng.random() >= 0.2]
        delivered.extend(rng.choice(events) for _ in range(rng.randrange(4)))
        rng.shuffle(delivered)
        harness.deliveries["user-a"] = delivered

        assert all(event.tenant_id == "tenant-a" for event in delivered)
        assert all(set(vars(event)) == {"tenant_id", "table", "event"} for event in delivered)
        assert harness.deliveries["user-b"] == []
        # One debounced authoritative read reconciles every delivery shape.
        final_projection = harness.authoritative_refetch("user-a", "tenant-a")
        assert final_projection == count
        assert harness.refetches["user-a"] == 1

        # Tenant switch removes the old private subscription before the next
        # tenant is mounted; old-tenant invalidations cannot cross the boundary.
        harness.unsubscribe("user-a", "tenant-a")
        harness.subscribe("user-a", "tenant-b")
        before = len(harness.deliveries["user-a"])
        harness.mutate("tenant-a", "deals", count + 1)
        harness.mutate("tenant-b", "deals", 1)
        assert len(harness.deliveries["user-a"]) == before + 1
        assert harness.deliveries["user-a"][-1].tenant_id == "tenant-b"


def test_task6_portal_uses_invalidation_and_bounded_fallback_only():
    realtime = (PORTAL / "lib/crmRealtime.ts").read_text(encoding="utf-8")
    api = (PORTAL / "lib/api.ts").read_text(encoding="utf-8")
    crm = (PORTAL / "lib/crm.ts").read_text(encoding="utf-8")
    live = (PORTAL / "lib/live.ts").read_text(encoding="utf-8")
    assert "invalidateApiCache(" in realtime
    assert "disposeTenant" in realtime
    assert "beginTenantSwitch" in api
    assert "ACTIVE_REQUESTS" in api and "AbortController" in api
    assert 'from "./api"' in crm and "requestApi" in crm
    assert "setVisibleInterval" in live and "visibilitychange" in live
    assert "deadlineMs" in live and "onDeadline" in live
    assert "provider" not in realtime.lower()
    legacy = (ROOT / "supabase/migrations/20260930000800_leadx_8_frontend_runtime.sql").read_text(encoding="utf-8").lower()
    assert "create trigger leadx_event_broadcast" in legacy
    assert "alter publication supabase_realtime add table" in legacy
    assert "email_sequence_steps" not in legacy
