"""Focused tests for the disposable local/test validation boundary.

These tests use only the in-memory fixture mechanism. They do not import
application services, read environment files, contact providers, or create
remote/durable work.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.fixtures.disposable_validation import (
    FixtureBoundaryError,
    FixtureRequest,
    GuardParity,
    InMemoryFixtureStore,
    provision_fixture,
)


def request(run_tag: str = "test-fixture-001", **overrides) -> FixtureRequest:
    values = {
        "profile": "disposable-local-test",
        "run_tag": run_tag,
        "tenant_id": f"synthetic-tenant-{run_tag}",
        "tenant_tag": f"synthetic-tenant-{run_tag}",
        "icp_id": f"synthetic-icp-{run_tag}",
        "icp_tag": f"synthetic-icp-{run_tag}",
        "cleanup_proven": True,
        "guards_restored": True,
    }
    values.update(overrides)
    return FixtureRequest(**values)


def test_provision_is_synthetic_tagged_and_pipeline_requires_guard_parity():
    store = InMemoryFixtureStore()
    fixture = provision_fixture(request(), store=store)

    pipeline = fixture.create_pipeline(target=5)

    assert {row.kind for row in store.rows} == {"tenant", "icp", "pipeline"}
    assert all(row.run_tag == fixture.run_tag for row in store.rows)
    assert pipeline.tenant_id == fixture.tenant_id
    assert pipeline.icp_id == fixture.icp_id
    assert fixture._guard_checked is True


def test_every_fixture_artifact_is_tagged_and_cleanup_is_bounded():
    store = InMemoryFixtureStore()
    fixture = provision_fixture(request(), store=store)
    fixture.add_artifact(f"evidence-{fixture.run_tag}")
    store.rows.append(
        type(store.rows[0])(
            kind="other-run",
            record_id="pipeline-other",
            run_tag="test-other-001",
            tenant_id="synthetic-tenant-test-other-001",
            icp_id="synthetic-icp-test-other-001",
        )
    )

    result = fixture.cleanup()

    assert result["status"] == "PASS"
    assert result["rows_removed"] == 2
    assert result["artifacts_removed"] == 1
    assert [row.run_tag for row in store.rows] == ["test-other-001"]


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("profile", "production", "profile-not-disposable-local-test"),
        ("profile", "staging", "profile-not-disposable-local-test"),
        ("tenant_tag", None, "missing-synthetic-tenant-tag"),
        ("icp_tag", None, "missing-synthetic-icp-tag"),
        ("auth_bypassed", True, "auth-rls-tenant-bypass"),
        ("rls_bypassed", True, "auth-rls-tenant-bypass"),
        ("tenant_checks_bypassed", True, "auth-rls-tenant-bypass"),
        ("provider_escape", True, "provider-escape"),
        ("target", 501, "cap-above-500"),
        ("records_bounded", False, "records-unbounded"),
        ("forbidden_action", "ses", "forbidden-action:ses"),
        ("cleanup_proven", False, "cleanup-unproven"),
        ("guards_restored", False, "guard-restoration-unproven"),
    ],
)
def test_unsafe_setup_is_rejected_before_fixture_rows(field, value, reason):
    store = InMemoryFixtureStore()
    with pytest.raises(FixtureBoundaryError) as raised:
        provision_fixture(request(**{field: value}), store=store)

    assert reason in raised.value.reasons
    assert store.rows == []
    assert store.artifacts == []
    assert store.used_run_tags == set()


def test_approved_real_tenant_and_unapproved_mechanism_are_rejected():
    store = InMemoryFixtureStore()
    with pytest.raises(FixtureBoundaryError) as real_tenant:
        provision_fixture(
            request(tenant_id="synthetic-real-tenant-marker", approved_real_tenant_ids=frozenset({"synthetic-real-tenant-marker"})),
            store=store,
        )
    assert "approved-real-tenant" in real_tenant.value.reasons
    assert store.rows == []

    with pytest.raises(FixtureBoundaryError) as mechanism:
        provision_fixture(request(mechanism="remote-database"), store=store)
    assert "unapproved-fixture-mechanism" in mechanism.value.reasons
    assert store.rows == []


def test_missing_or_duplicate_run_tags_fail_closed():
    store = InMemoryFixtureStore()
    with pytest.raises(FixtureBoundaryError):
        provision_fixture(request(run_tag=""), store=store)
    assert store.rows == []

    provision_fixture(request(), store=store)
    with pytest.raises(FixtureBoundaryError) as duplicate:
        provision_fixture(request(), store=store)
    assert "missing-or-non-unique-run-tag" in duplicate.value.reasons
    assert len(store.rows) == 2


def test_guard_parity_failure_prevents_pipeline_creation():
    fixture = provision_fixture(request())
    fixture.guard_parity = replace(fixture.guard_parity, rls_active=False)

    with pytest.raises(FixtureBoundaryError) as raised:
        fixture.create_pipeline()

    assert "rls-not-active" in raised.value.reasons
    assert [row.kind for row in fixture.store.rows] == ["tenant", "icp"]
    assert fixture._guard_checked is False


def test_cap_500_is_allowed_but_501_is_rejected_before_work():
    fixture = provision_fixture(request(target=500))
    assert fixture.create_pipeline(target=500).payload["target"] == 500

    with pytest.raises(FixtureBoundaryError) as raised:
        provision_fixture(request("test-fixture-502", target=501))
    assert "cap-above-500" in raised.value.reasons


def test_guard_snapshot_requires_all_parity_controls():
    fixture = provision_fixture(request())
    fixture.guard_parity = GuardParity(production_guards_active=False)

    with pytest.raises(FixtureBoundaryError) as raised:
        fixture.assert_guard_parity()

    assert raised.value.reasons == ("production-guards-not-active",)


def test_provider_escape_and_forbidden_actions_are_denied_without_work():
    fixture = provision_fixture(request())

    with pytest.raises(FixtureBoundaryError) as escaped:
        fixture.assert_provider_allowed("remote-provider")
    with pytest.raises(FixtureBoundaryError) as forbidden:
        fixture.assert_provider_allowed("ses")

    assert escaped.value.reasons == ("provider-escape",)
    assert forbidden.value.reasons == ("forbidden-action:ses",)
    assert [row.kind for row in fixture.store.rows] == ["tenant", "icp"]
