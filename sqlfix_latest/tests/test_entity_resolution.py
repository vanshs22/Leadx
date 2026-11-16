"""Offline entity identity, tenant projection, and merge-review behavior."""
from __future__ import annotations

import pytest

from app.orchestration.entities import (
    CanonicalEntity, EntityScopeError, project_for_tenant, resolve_entity,
)
from app.orchestration.merge_review import (
    InMemoryMergeReviewStore, create_merge_review, decide_merge_review,
)


def test_duplicate_domain_resolves_to_same_canonical_entity_without_tenant_data():
    existing = CanonicalEntity.from_record({"business_name": "Acme Gym", "domain": "https://www.acme.example/path"})
    result = resolve_entity({"name": "ACME GYM", "website": "acme.example"}, [existing], provenance={"source": "directory"})

    assert result.decision == "same_entity"
    assert result.entity == existing
    assert "domain" in result.match_keys
    assert result.provenance == {"source": "directory"}
    assert not hasattr(result.entity, "tenant_id")


def test_weak_match_becomes_review_and_review_preserves_both_records_and_is_idempotent():
    left = {"id": "source-a", "name": "Corner Fitness", "address": "1 Main Street"}
    right = {"id": "source-b", "name": "Corner Fitness", "address": "1 Main Street"}
    canonical = CanonicalEntity.from_record(left)
    result = resolve_entity(right, [canonical])
    assert result.decision == "same_entity"

    # Name-only evidence is deliberately uncertain.
    possible = resolve_entity({"id": "source-c", "name": "Corner Fitness"}, [canonical], provenance={"source": "search"})
    assert possible.decision == "possible_match"
    review = create_merge_review(possible, left, {**right, "id": "source-c"}, tenant_id="tenant-a", use_case_id="gym", created_at="2026-01-01T00:00:00Z")
    store = InMemoryMergeReviewStore()
    assert store.save(review) is review
    assert store.save(review) is review
    assert store.get(review.review_id).source_record_a["id"] == "source-a"
    decided = store.decide(review.review_id, "do_not_merge", reviewer_id="operator-1", decided_at="2026-01-02T00:00:00Z")
    assert decided.decision == "do_not_merge"
    assert decided.reviewer_id == "operator-1"
    assert decide_merge_review(decided, "same_entity", reviewer_id="operator-2", decided_at="2026-01-03T00:00:00Z").decision == "same_entity"


def test_conflicting_strong_keys_require_review_and_scope_guards_apply_before_projection():
    existing = CanonicalEntity.from_record({"name": "North Gym", "domain": "north.example", "phone": "+1 555 010 1000"})
    result = resolve_entity({"name": "North Gym", "domain": "other.example", "phone": "+1 555 010 1000"}, [existing])
    assert result.decision == "conflict"
    assert result.review_required is True

    projection = project_for_tenant(existing, "tenant-a", "use-case-a", policy={"authorized": True, "compliance_allowed": True, "fresh": True}, idempotency_key="projection-1")
    replay = project_for_tenant(existing, "tenant-a", "use-case-a", policy={"authorized": True, "compliance_allowed": True, "fresh": True}, idempotency_key="projection-1")
    assert projection.projection_id == replay.projection_id
    assert projection.tenant_id == "tenant-a"
    with pytest.raises(EntityScopeError):
        project_for_tenant(existing, "tenant-a", "use-case-a", policy={"suppressed": True})
    with pytest.raises(EntityScopeError):
        project_for_tenant(existing, "tenant-a", "use-case-a", policy={"fresh": False})
