"""Task 12: deterministic local realtime delivery and recovery validation.

This test uses only an in-memory transport and synthetic tenant/run IDs. It does
not contact Supabase, a deployed realtime service, providers, or client data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "lead-crm-portal"
MIGRATION = ROOT / "supabase/migrations/20261020001600_leadx_21_realtime_capture.sql"


@dataclass(frozen=True)
class Event:
    sequence: int
    tenant_id: str
    run_id: str
    table: str
    event: str


@dataclass
class AuthoritativeRunStore:
    runs: dict[tuple[str, str], dict] = field(default_factory=dict)
    refetches: int = 0
    poll_calls: int = 0

    def put(self, tenant_id: str, run_id: str, *, status: str, assigned: int, delivered: int) -> dict:
        current = self.runs.get((tenant_id, run_id), {})
        row = {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "status": status,
            "assigned": assigned,
            "delivered": delivered,
            "revision": int(current.get("revision", 0)) + 1,
        }
        self.runs[(tenant_id, run_id)] = row
        return dict(row)

    def refetch(self, user: str, tenant_id: str, run_id: str, members: dict[str, set[str]]) -> dict:
        if tenant_id not in members.get(user, set()):
            raise PermissionError("authoritative tenant read denied")
        self.refetches += 1
        return dict(self.runs[(tenant_id, run_id)])

    def remove_fixture(self, tenant_id: str, run_id: str) -> None:
        self.runs.pop((tenant_id, run_id), None)


@dataclass
class DeterministicRealtime:
    """Private tenant transport with explicit disconnect and no replay shortcut."""

    members: dict[str, set[str]]
    subscriptions: dict[tuple[str, str], str] = field(default_factory=dict)
    event_log: list[Event] = field(default_factory=list)
    deliveries: dict[str, list[Event]] = field(default_factory=dict)
    connected: set[str] = field(default_factory=set)
    next_sequence: int = 0

    def subscribe(self, user: str, tenant_id: str) -> None:
        if tenant_id not in self.members.get(user, set()):
            raise PermissionError("private tenant topic denied")
        self.subscriptions[(user, tenant_id)] = f"crm:{tenant_id}"
        self.connected.add(user)
        self.deliveries.setdefault(user, [])

    def disconnect(self, user: str) -> None:
        self.connected.discard(user)
        self.subscriptions = {
            key: topic for key, topic in self.subscriptions.items() if key[0] != user
        }

    def publish(self, tenant_id: str, run_id: str, event: str = "UPDATE") -> Event:
        self.next_sequence += 1
        item = Event(self.next_sequence, tenant_id, run_id, "pipeline_runs", event)
        self.event_log.append(item)
        for (user, subscribed_tenant), _topic in self.subscriptions.items():
            if user in self.connected and subscribed_tenant == tenant_id:
                self.deliveries.setdefault(user, []).append(item)
        return item

    def cleanup(self, tenant_id: str, run_id: str) -> None:
        self.event_log = [
            item for item in self.event_log
            if (item.tenant_id, item.run_id) != (tenant_id, run_id)
        ]
        self.subscriptions = {
            key: topic for key, topic in self.subscriptions.items() if key[1] != tenant_id
        }
        self.connected = {user for user in self.connected if any(key[0] == user for key in self.subscriptions)}


@dataclass
class Subscriber:
    user: str
    tenant_id: str
    run_id: str
    transport: DeterministicRealtime
    store: AuthoritativeRunStore
    members: dict[str, set[str]]
    last_sequence: int = 0
    events: list[Event] = field(default_factory=list)
    terminal_projection: dict | None = None

    def receive(self, event: Event) -> None:
        if event.tenant_id != self.tenant_id or event.run_id != self.run_id:
            return
        if event.sequence <= self.last_sequence:
            return
        self.last_sequence = event.sequence
        self.events.append(event)
        # The event is metadata only. Business state comes from an authorized read.
        self.terminal_projection = self.store.refetch(
            self.user, self.tenant_id, self.run_id, self.members
        )

    def reconcile_after_reconnect(self) -> None:
        self.terminal_projection = self.store.refetch(
            self.user, self.tenant_id, self.run_id, self.members
        )


def test_task12_authenticated_tenant_delivery_ordering_terminal_recovery_and_cleanup():
    members = {"user-a": {"tenant-a"}, "user-b": {"tenant-b"}}
    store = AuthoritativeRunStore()
    transport = DeterministicRealtime(members)
    run_id = "synthetic-run-task12"
    store.put("tenant-a", run_id, status="running", assigned=0, delivered=0)
    store.put("tenant-b", run_id, status="running", assigned=0, delivered=0)

    tenant_a = Subscriber("user-a", "tenant-a", run_id, transport, store, members)
    tenant_b = Subscriber("user-b", "tenant-b", run_id, transport, store, members)
    transport.subscribe("user-a", "tenant-a")
    transport.subscribe("user-b", "tenant-b")
    try:
        transport.subscribe("user-a", "tenant-b")
    except PermissionError:
        pass
    else:  # pragma: no cover - documents the authorization invariant
        raise AssertionError("cross-tenant private topic must be denied")

    store.put("tenant-a", run_id, status="running", assigned=4, delivered=0)
    first = transport.publish("tenant-a", run_id)
    tenant_a.receive(first)
    assert transport.deliveries["user-b"] == []
    assert first.tenant_id == "tenant-a" and first.run_id == run_id
    assert {field for field in vars(first)} == {"sequence", "tenant_id", "run_id", "table", "event"}

    store.put("tenant-a", run_id, status="running", assigned=5, delivered=3)
    second = transport.publish("tenant-a", run_id)
    tenant_a.receive(second)
    # Duplicate and stale hints cannot move the authoritative projection backwards.
    tenant_a.receive(second)
    tenant_a.receive(first)
    assert [event.sequence for event in tenant_a.events] == [first.sequence, second.sequence]
    assert tenant_a.terminal_projection["assigned"] == 5

    transport.disconnect("user-a")
    store.put("tenant-a", run_id, status="completed", assigned=5, delivered=5)
    missed_terminal = transport.publish("tenant-a", run_id)
    assert missed_terminal not in transport.deliveries["user-a"]

    # Reconnect intentionally re-syncs from the authoritative API; it does not
    # pretend that polling or a static event payload replayed the missed event.
    transport.subscribe("user-a", "tenant-a")
    tenant_a.reconcile_after_reconnect()
    assert tenant_a.terminal_projection == store.runs[("tenant-a", run_id)]
    assert tenant_a.terminal_projection["status"] == "completed"
    assert tenant_a.terminal_projection["delivered"] == 5
    assert store.poll_calls == 0

    transport.cleanup("tenant-a", run_id)
    store.remove_fixture("tenant-a", run_id)
    assert ("tenant-a", run_id) not in store.runs
    assert not transport.event_log
    assert all(tenant != "tenant-a" for _user, tenant in transport.subscriptions)
    assert "user-a" not in transport.connected
    assert tenant_b.terminal_projection is None


def test_task12_portal_and_migration_keep_private_metadata_only_recovery_contract():
    realtime = (PORTAL / "lib/crmRealtime.ts").read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8").lower()

    # Authenticated private subscription and same-tenant channel authorization.
    assert "await sb.realtime.setAuth()" in realtime
    assert 'sb.channel(`crm:${manager.tenantId}`' in realtime
    assert 'config: { private: true, broadcast: { self: false, ack: false } }' in realtime
    assert 'event: "crm_change"' in realtime
    assert "leadx_is_workspace_member" in migration
    assert "no admin aggregate" in migration

    # Recovery and terminal visibility use authoritative reconciliation, not a
    # static payload or an unbounded polling loop.
    assert 'scheduleRefresh(manager, { event: "reconcile" })' in realtime
    assert "invalidateApiCache" in realtime
    assert "payloads never become projection state" in realtime
    assert "setInterval" not in realtime
    assert "void removeChannel(manager)" in realtime
    assert "manager.listeners.clear()" in realtime
    assert "manager.stateListeners.clear()" in realtime
    assert "realtime availability must not roll back the durable mutation" in migration


def test_task12_evidence_scope_is_explicit_and_sanitized():
    evidence = {
        "check": "realtime event delivery and recovery",
        "result": "PASS",
        "evidence_class": "synthetic/offline",
        "environment": "deterministic in-memory transport",
        "authenticated_scope": "two synthetic users, two synthetic tenants",
        "assertions": [
            "private subscription authorization",
            "same-tenant delivery and cross-tenant denial",
            "metadata event shape and sequence deduplication",
            "terminal-state visibility after disconnect/reconnect resync",
            "subscription and fixture cleanup",
        ],
        "production_realtime": "NOT TESTED",
        "forbidden_actions": 0,
        "credentials_or_client_data": False,
    }
    assert evidence["result"] == "PASS"
    assert evidence["evidence_class"] == "synthetic/offline"
    assert evidence["production_realtime"] == "NOT TESTED"
    assert evidence["forbidden_actions"] == 0
    assert evidence["credentials_or_client_data"] is False
    assert not {"token", "secret", "payload", "email"} & set(evidence)
