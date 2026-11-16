"""Fail-closed in-memory boundary for disposable local validation.

This module is deliberately test-only. It does not read configuration, use a
network client, create a database connection, or provide an authentication or
RLS bypass. Callers must explicitly opt into the exact disposable profile and
supply a unique run tag; every owned record carries that tag for cleanup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Mapping


PROFILE = "disposable-local-test"
LOCAL_PROVIDER = "synthetic-local"
MAX_TARGET = 500
FORBIDDEN_ACTIONS = frozenset({"email", "outreach", "campaign", "stripe", "ses"})
_RUN_TAG = re.compile(r"^test-[a-z0-9][a-z0-9-]{3,62}$")
_SYNTHETIC_ID = re.compile(r"^synthetic-[a-z0-9][a-z0-9-]{1,63}$")


class FixtureBoundaryError(ValueError):
    """Raised when a fixture request would weaken a safety boundary."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__("unsafe disposable fixture: " + ", ".join(reasons))


@dataclass(frozen=True)
class FixtureRequest:
    """Explicit, secret-free inputs for an in-memory fixture scope."""

    profile: str
    run_tag: str
    tenant_id: str
    tenant_tag: str
    icp_id: str
    icp_tag: str
    target: int = 5
    mechanism: str = "in-memory"
    auth_bypassed: bool = False
    rls_bypassed: bool = False
    tenant_checks_bypassed: bool = False
    provider_isolated: bool = True
    provider_escape: bool = False
    records_bounded: bool = True
    cleanup_proven: bool = False
    guards_restored: bool = False
    forbidden_action: str | None = None
    approved_real_tenant_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GuardParity:
    """The guard state required before a pipeline row may be created."""

    authentication_active: bool = True
    rls_active: bool = True
    tenant_checks_active: bool = True
    provider_isolated: bool = True
    forbidden_actions_denied: bool = True
    uniqueness_active: bool = True
    target_cap: int = MAX_TARGET
    cleanup_addressable: bool = True
    production_guards_active: bool = True


@dataclass(frozen=True)
class FixtureRecord:
    kind: str
    record_id: str
    run_tag: str
    tenant_id: str
    icp_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayRecord:
    """Sanitized durable identity owned by one disposable run."""

    run_tag: str
    idempotency_key: str
    tenant_id: str
    icp_id: str
    identity: str
    request_hash: str
    status: str = "active"


@dataclass
class InMemoryFixtureStore:
    """Approved local-only store; it has no remote or durable backing."""

    rows: list[FixtureRecord] = field(default_factory=list)
    artifacts: list[FixtureRecord] = field(default_factory=list)
    used_run_tags: set[str] = field(default_factory=set)
    replay_records: dict[str, ReplayRecord] = field(default_factory=dict)
    provider_execution_ids: set[str] = field(default_factory=set)


@dataclass
class DisposableFixture:
    """A tag-owned synthetic tenant/ICP scope for local tests."""

    request: FixtureRequest
    store: InMemoryFixtureStore
    guard_parity: GuardParity
    tenant_id: str
    icp_id: str
    _guard_checked: bool = False
    _pipeline_created: bool = False

    @property
    def run_tag(self) -> str:
        return self.request.run_tag

    def assert_guard_parity(self) -> GuardParity:
        """Fail closed before any pipeline work is admitted."""

        reasons = _guard_rejection_reasons(self.request, self.guard_parity)
        if reasons:
            raise FixtureBoundaryError(tuple(reasons))
        self._guard_checked = True
        return self.guard_parity

    def assert_provider_allowed(self, provider: str) -> str:
        """Allow only the deny-all-safe synthetic provider path."""

        normalized = str(provider).lower()
        if normalized in FORBIDDEN_ACTIONS:
            raise FixtureBoundaryError((f"forbidden-action:{normalized}",))
        if normalized != LOCAL_PROVIDER or not self.guard_parity.provider_isolated:
            raise FixtureBoundaryError(("provider-escape",))
        return LOCAL_PROVIDER

    def create_pipeline(self, *, target: int | None = None) -> FixtureRecord:
        """Create one synthetic pipeline row only after parity assertions."""

        self.assert_guard_parity()
        provider = self.assert_provider_allowed(LOCAL_PROVIDER)
        requested_target = self.request.target if target is None else target
        if requested_target < 1 or requested_target > MAX_TARGET:
            raise FixtureBoundaryError(("cap-above-500" if requested_target > MAX_TARGET else "target-must-be-positive",))
        if self._pipeline_created:
            raise FixtureBoundaryError(("duplicate-pipeline-work",))
        record = FixtureRecord(
            kind="pipeline",
            record_id=f"pipeline-{self.run_tag}",
            run_tag=self.run_tag,
            tenant_id=self.tenant_id,
            icp_id=self.icp_id,
            payload={"target": requested_target, "provider": provider},
        )
        self.store.rows.append(record)
        self._pipeline_created = True
        return record

    def add_artifact(self, artifact_id: str, *, kind: str = "evidence") -> FixtureRecord:
        """Add a sanitized, run-tagged artifact addressable by cleanup."""

        if not artifact_id or self.run_tag not in artifact_id:
            raise FixtureBoundaryError(("artifact-missing-run-tag",))
        artifact = FixtureRecord(
            kind=kind,
            record_id=artifact_id,
            run_tag=self.run_tag,
            tenant_id=self.tenant_id,
            icp_id=self.icp_id,
        )
        self.store.artifacts.append(artifact)
        return artifact

    def cleanup(self) -> dict[str, int | str]:
        """Remove only this scope's tagged rows and artifacts."""

        if not self.request.cleanup_proven or not self.request.guards_restored:
            raise FixtureBoundaryError(("cleanup-or-guard-restoration-unproven",))
        before_rows = len(self.store.rows)
        before_artifacts = len(self.store.artifacts)
        before_replays = len(self.store.replay_records)
        self.store.rows[:] = [row for row in self.store.rows if row.run_tag != self.run_tag]
        self.store.artifacts[:] = [item for item in self.store.artifacts if item.run_tag != self.run_tag]
        self.store.replay_records = {
            key: record
            for key, record in self.store.replay_records.items()
            if record.run_tag != self.run_tag
        }
        self.store.provider_execution_ids = {
            identity
            for identity in self.store.provider_execution_ids
            if any(record.identity == identity for record in self.store.replay_records.values())
        }
        removed_rows = before_rows - len(self.store.rows)
        removed_artifacts = before_artifacts - len(self.store.artifacts)
        removed_replays = before_replays - len(self.store.replay_records)
        self.store.used_run_tags.discard(self.run_tag)
        return {
            "status": "PASS",
            "run_tag": self.run_tag,
            "rows_removed": removed_rows,
            "artifacts_removed": removed_artifacts,
            "replay_records_removed": removed_replays,
        }


@dataclass
class DurableReplayStore:
    """Deterministic replay authority backed by the local fixture store only."""

    store: InMemoryFixtureStore

    @property
    def records(self) -> dict[str, ReplayRecord]:
        return self.store.replay_records

    @property
    def active_work_count(self) -> int:
        return sum(record.status == "active" for record in self.records.values())

    @property
    def provider_executions(self) -> int:
        return len(self.store.provider_execution_ids)

    def submit(
        self,
        fixture: DisposableFixture,
        *,
        idempotency_key: str,
        tenant_id: str,
        payload: Mapping[str, Any],
        target: int | None = None,
        provider: str = LOCAL_PROVIDER,
    ) -> dict[str, str]:
        """Return a stable identity or an opaque, fail-closed rejection."""

        fixture.assert_guard_parity()
        fixture.assert_provider_allowed(provider)
        requested_target = fixture.request.target if target is None else target
        if requested_target < 1 or requested_target > MAX_TARGET:
            raise FixtureBoundaryError(("cap-above-500" if requested_target > MAX_TARGET else "target-must-be-positive",))

        request_hash = _replay_request_hash(fixture.icp_id, requested_target, payload)
        existing = self.records.get(idempotency_key)
        if existing is not None:
            if (
                existing.tenant_id == tenant_id
                and existing.icp_id == fixture.icp_id
                and existing.request_hash == request_hash
            ):
                return {"status": existing.status, "identity": existing.identity}
            return {"status": "conflict", "reason_code": "idempotency_mismatch"}

        if tenant_id != fixture.tenant_id:
            return {"status": "rejected", "reason_code": "tenant_mismatch"}

        identity_material = f"{fixture.run_tag}|{idempotency_key}|{request_hash}"
        identity = f"replay-{fixture.run_tag}-{hashlib.sha256(identity_material.encode()).hexdigest()[:16]}"
        if any(record.identity == identity for record in self.records.values()):
            raise FixtureBoundaryError(("duplicate-replay-identity",))
        record = ReplayRecord(
            run_tag=fixture.run_tag,
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            icp_id=fixture.icp_id,
            identity=identity,
            request_hash=request_hash,
        )
        self.records[idempotency_key] = record
        self.store.provider_execution_ids.add(identity)
        return {"status": "active", "identity": identity}

    def complete(self, idempotency_key: str) -> None:
        self._set_status(idempotency_key, "terminal")

    def fence(self, idempotency_key: str) -> None:
        self._set_status(idempotency_key, "fenced")

    def sanitized_evidence(self, fixture: DisposableFixture) -> list[dict[str, str | bool]]:
        """Expose only fingerprints, statuses, counts, evidence class, and tag."""

        return [
            {
                "run_tag": record.run_tag,
                "identity_fingerprint": hashlib.sha256(record.identity.encode()).hexdigest()[:16],
                "status": record.status,
                "active": record.status == "active",
                "evidence_class": "local",
            }
            for record in self.records.values()
            if record.run_tag == fixture.run_tag
        ]

    def _set_status(self, idempotency_key: str, status: str) -> None:
        record = self.records.get(idempotency_key)
        if record is None:
            raise FixtureBoundaryError(("unknown-replay-identity",))
        self.records[idempotency_key] = ReplayRecord(
            run_tag=record.run_tag,
            idempotency_key=record.idempotency_key,
            tenant_id=record.tenant_id,
            icp_id=record.icp_id,
            identity=record.identity,
            request_hash=record.request_hash,
            status=status,
        )


def _replay_request_hash(icp_id: str, target: int, payload: Mapping[str, Any]) -> str:
    material = json.dumps(
        {"icp_id": icp_id, "target": target, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def provision_fixture(request: FixtureRequest, *, store: InMemoryFixtureStore | None = None) -> DisposableFixture:
    """Provision only a synthetic, tag-owned fixture in the approved local store."""

    fixture_store = store or InMemoryFixtureStore()
    reasons = _provision_rejection_reasons(request, fixture_store)
    if reasons:
        raise FixtureBoundaryError(tuple(reasons))

    fixture_store.used_run_tags.add(request.run_tag)
    tenant = FixtureRecord(
        kind="tenant",
        record_id=request.tenant_id,
        run_tag=request.run_tag,
        tenant_id=request.tenant_id,
        icp_id=request.icp_id,
        payload={"tag": request.tenant_tag},
    )
    icp = FixtureRecord(
        kind="icp",
        record_id=request.icp_id,
        run_tag=request.run_tag,
        tenant_id=request.tenant_id,
        icp_id=request.icp_id,
        payload={"tag": request.icp_tag},
    )
    fixture_store.rows.extend((tenant, icp))
    return DisposableFixture(
        request=request,
        store=fixture_store,
        guard_parity=GuardParity(
            provider_isolated=request.provider_isolated and not request.provider_escape,
            cleanup_addressable=request.records_bounded,
        ),
        tenant_id=request.tenant_id,
        icp_id=request.icp_id,
    )


def _provision_rejection_reasons(request: FixtureRequest, store: InMemoryFixtureStore) -> list[str]:
    reasons: list[str] = []
    if request.profile != PROFILE:
        reasons.append("profile-not-disposable-local-test")
    if request.mechanism != "in-memory":
        reasons.append("unapproved-fixture-mechanism")
    if not _RUN_TAG.fullmatch(request.run_tag) or request.run_tag in store.used_run_tags:
        reasons.append("missing-or-non-unique-run-tag")
    if not request.tenant_tag or not request.tenant_tag.startswith("synthetic-tenant-") or request.run_tag not in request.tenant_tag:
        reasons.append("missing-synthetic-tenant-tag")
    if not request.icp_tag or not request.icp_tag.startswith("synthetic-icp-") or request.run_tag not in request.icp_tag:
        reasons.append("missing-synthetic-icp-tag")
    if not _SYNTHETIC_ID.fullmatch(request.tenant_id):
        reasons.append("tenant-not-synthetic")
    elif request.run_tag not in request.tenant_id:
        reasons.append("tenant-not-run-scoped")
    if not _SYNTHETIC_ID.fullmatch(request.icp_id):
        reasons.append("icp-not-synthetic")
    elif request.run_tag not in request.icp_id:
        reasons.append("icp-not-run-scoped")
    if request.tenant_id in request.approved_real_tenant_ids:
        reasons.append("approved-real-tenant")
    if request.auth_bypassed or request.rls_bypassed or request.tenant_checks_bypassed:
        reasons.append("auth-rls-tenant-bypass")
    if not request.provider_isolated or request.provider_escape:
        reasons.append("provider-escape")
    if request.target > MAX_TARGET:
        reasons.append("cap-above-500")
    elif request.target < 1:
        reasons.append("target-must-be-positive")
    if not request.records_bounded:
        reasons.append("records-unbounded")
    if request.forbidden_action and request.forbidden_action.lower() in FORBIDDEN_ACTIONS:
        reasons.append(f"forbidden-action:{request.forbidden_action.lower()}")
    if not request.cleanup_proven:
        reasons.append("cleanup-unproven")
    if not request.guards_restored:
        reasons.append("guard-restoration-unproven")
    return reasons


def _guard_rejection_reasons(request: FixtureRequest, parity: GuardParity) -> list[str]:
    reasons: list[str] = []
    if request.auth_bypassed or not parity.authentication_active:
        reasons.append("authentication-not-active")
    if request.rls_bypassed or not parity.rls_active:
        reasons.append("rls-not-active")
    if request.tenant_checks_bypassed or not parity.tenant_checks_active:
        reasons.append("tenant-checks-not-active")
    if not parity.provider_isolated:
        reasons.append("provider-isolation-missing")
    if not parity.forbidden_actions_denied:
        reasons.append("forbidden-actions-not-denied")
    if not parity.uniqueness_active:
        reasons.append("uniqueness-not-active")
    if parity.target_cap != MAX_TARGET or request.target > MAX_TARGET:
        reasons.append("cap-not-500")
    if not parity.cleanup_addressable or not request.records_bounded:
        reasons.append("cleanup-not-addressable")
    if not parity.production_guards_active:
        reasons.append("production-guards-not-active")
    if request.forbidden_action and request.forbidden_action.lower() in FORBIDDEN_ACTIONS:
        reasons.append(f"forbidden-action:{request.forbidden_action.lower()}")
    return reasons


__all__ = [
    "FORBIDDEN_ACTIONS",
    "GuardParity",
    "FixtureBoundaryError",
    "FixtureRecord",
    "FixtureRequest",
    "DisposableFixture",
    "DurableReplayStore",
    "InMemoryFixtureStore",
    "LOCAL_PROVIDER",
    "MAX_TARGET",
    "PROFILE",
    "ReplayRecord",
    "provision_fixture",
]
