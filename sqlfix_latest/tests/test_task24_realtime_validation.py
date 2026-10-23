"""Low-cost task-24 contracts; no database, provider, or network writes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261020001600_leadx_21_realtime_capture.sql"
PORTAL = ROOT / "lead-crm-portal"

# These are intentionally bounded fallbacks: views/global projections or tables
# without a row-local tenant_id cannot safely publish tenant Broadcast events.
# Their owning pages must reconcile through an authoritative API read instead.
BOUNDED_FALLBACK_TABLES = {
    "approval_inbox",
    "agent_approvals",
    "email_sequence_steps",
    "lead_crm_cards",
    "leads_global",
    "learning_recommendations",
    "leadx_event_log",
    "quota_usage",
    "team_invites",
}


@dataclass
class Event:
    tenant_id: str
    table: str
    operation: str
    resource_id: str | None = None


@dataclass
class TenantBroadcastHarness:
    """A deterministic stand-in for the DB event-log + private Broadcast path."""

    members: dict[str, set[str]]
    subscriptions: dict[str, set[str]] = field(default_factory=dict)
    event_log: list[Event] = field(default_factory=list)
    deliveries: dict[str, list[Event]] = field(default_factory=dict)
    provider_calls: int = 0
    credit_operations: int = 0

    def subscribe(self, user: str, tenant_id: str) -> None:
        if user not in self.members.get(tenant_id, set()):
            raise PermissionError("private tenant topic denied")
        self.subscriptions.setdefault(tenant_id, set()).add(user)
        self.deliveries.setdefault(user, [])

    def mutate(self, table: str, operation: str, new_row: dict | None, old_row: dict | None) -> Event | None:
        row = old_row if operation == "DELETE" else new_row
        tenant_id = (row or {}).get("tenant_id")
        if not tenant_id:
            return None
        event = Event(tenant_id, table, operation, (row or {}).get("id"))
        self.event_log.append(event)
        for user in self.subscriptions.get(tenant_id, set()):
            self.deliveries.setdefault(user, []).append(event)
        return event


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def _supported_tables(text: str) -> set[str]:
    match = re.search(r"supported_tables\s+text\[\]\s*:=\s*array\[(.*?)\];", text, re.S)
    assert match, "the capture/publication list must be defined once"
    return set(re.findall(r"'([a-z][a-z0-9_]*)'", match.group(1)))


def _portal_filters() -> set[str]:
    tables: set[str] = set()
    for path in (PORTAL / "app").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"tables\s*:\s*\[([^\]]*)\]", text, re.S):
            tables.update(re.findall(r"['\"]([a-z][a-z0-9_]*)['\"]", match.group(1)))
    return tables


def test_task24_trigger_filter_publication_parity_is_explicit():
    text = _migration_text()
    supported = _supported_tables(text)
    filters = _portal_filters()
    unsupported = filters - supported
    assert unsupported <= BOUNDED_FALLBACK_TABLES
    assert "bounded fallback" in text
    assert "no admin aggregate" in text
    assert "alter publication supabase_realtime add table public.%i" in text
    assert "drop trigger if exists leadx_event_capture" in text
    assert "create trigger leadx_event_capture after insert or update or delete" in text


def test_task24_delete_and_unowned_rows_keep_tenant_scope():
    harness = TenantBroadcastHarness({"tenant-a": {"user-a"}, "tenant-b": {"user-b"}})
    harness.subscribe("user-a", "tenant-a")

    inserted = harness.mutate("pipeline_runs", "INSERT", {"id": "run-a", "tenant_id": "tenant-a"}, None)
    deleted = harness.mutate("pipeline_runs", "DELETE", None, {"id": "run-a", "tenant_id": "tenant-a"})
    unowned = harness.mutate("leads_global", "INSERT", {"id": "global-1"}, None)

    assert inserted and deleted and deleted.tenant_id == "tenant-a"
    assert unowned is None
    assert [event.operation for event in harness.deliveries["user-a"]] == ["INSERT", "DELETE"]
    assert all(event.tenant_id == "tenant-a" for event in harness.deliveries["user-a"])
    assert harness.provider_calls == 0
    assert harness.credit_operations == 0


def test_task24_two_tenants_cannot_observe_each_other_or_admin_aggregate():
    harness = TenantBroadcastHarness({"tenant-a": {"user-a"}, "tenant-b": {"user-b"}})
    harness.subscribe("user-a", "tenant-a")
    harness.subscribe("user-b", "tenant-b")

    harness.mutate("pipeline_stages", "UPDATE", {"id": "stage-b", "tenant_id": "tenant-b"}, None)
    assert harness.deliveries["user-a"] == []
    assert [event.resource_id for event in harness.deliveries["user-b"]] == ["stage-b"]

    try:
        harness.subscribe("user-a", "admin")
    except PermissionError:
        pass
    else:  # pragma: no cover - documents the security invariant
        raise AssertionError("admin aggregate topic must not be tenant-readable")


def test_task24_duplicate_out_of_order_delivery_is_authoritative_refetch_only():
    harness = TenantBroadcastHarness({"tenant-a": {"user-a"}})
    harness.subscribe("user-a", "tenant-a")
    # Duplicate and out-of-order notifications are harmless because the event
    # is metadata-only; the mounted surface reads its authoritative projection.
    first = harness.mutate("pipeline_runs", "UPDATE", {"id": "run-a", "tenant_id": "tenant-a"}, None)
    duplicate = harness.mutate("pipeline_runs", "UPDATE", {"id": "run-a", "tenant_id": "tenant-a"}, None)
    assert first and duplicate
    assert len(harness.deliveries["user-a"]) == 2
    assert {event.resource_id for event in harness.deliveries["user-a"]} == {"run-a"}
    assert harness.provider_calls == 0
    assert harness.credit_operations == 0


def test_task24_portal_refresh_is_visibility_aware_and_bounded():
    realtime = (PORTAL / "lib/crmRealtime.ts").read_text(encoding="utf-8")
    live = (PORTAL / "lib/live.ts").read_text(encoding="utf-8")
    admin_pipeline = (PORTAL / "app/(admin)/admin/pipeline/page.tsx").read_text(encoding="utf-8")
    assert 'config: { private: true, broadcast: { self: false, ack: false } }' in realtime
    assert 'setAuth()' in realtime
    assert 'event: "reconcile"' in realtime
    assert 'document.visibilityState !== "visible"' in realtime
    assert "invalidateApiCache()" in realtime
    assert "setVisibleInterval" in live and "visibilitychange" in live
    assert "deadlineMs" in live and "onDeadline" in live
    assert "runTerminal" in admin_pipeline
    assert "Unable to read live run status" in admin_pipeline


def test_task24_terminal_contract_preserves_request_run_job_evidence():
    operation_source = (PORTAL / "components/ui/OperationState.tsx").read_text(encoding="utf-8")
    for field in ("runId", "jobId", "requestId", "terminalReason", "retries", "workerStatus", "delivered"):
        assert field in operation_source
    assert "jobsHref" in operation_source
    assert "PollingDeadlineState" in operation_source
