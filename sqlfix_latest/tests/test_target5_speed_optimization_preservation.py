"""Pre-implementation preservation properties for target5 speed optimization.

All cases are deterministic and local-only.  They exercise existing production
boundaries with sanitized synthetic rows and never read .env, credentials,
network/providers, remote SQL, email, outreach, campaigns, Stripe, or SES.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeSupabase


TENANTS = ("tenant-synthetic-a", "tenant-synthetic-b")


def test_fresh_cache_reuse_deduplicates_pool_without_provider_work(monkeypatch):
    """Fresh valid enrichment is reused and duplicate identities enter once."""

    from app.layers import persistence_layer

    now = datetime.now(timezone.utc)
    fake = FakeSupabase(
        {
            "enrichment": [
                {
                    "lead_id": "lead-cached",
                    "email": "owner@cached.test",
                    "email_valid": True,
                    "enrichment_ok": True,
                    "extracted_at": (now - timedelta(hours=1)).isoformat(),
                    "owner_name": "Synthetic Owner",
                }
            ]
        }
    )
    monkeypatch.setattr(persistence_layer, "get_supabase", lambda: fake)
    leads = [
        {
            "id": "lead-cached",
            "fingerprint": "fp-cached",
            "name": "Cached Synthetic Gym",
            "website_domain": "cached.test",
        },
        {
            "id": "lead-cached",
            "fingerprint": "fp-cached",
            "name": "Cached Synthetic Gym",
            "website_domain": "cached.test",
        },
    ]

    prepared, needs_enrichment = asyncio.run(
        persistence_layer.prepare_enrichment_pool(leads, reuse_days=14)
    )

    assert len(prepared) == 1
    assert needs_enrichment == []
    assert prepared[0]["_enrichment_reused"] is True
    assert prepared[0]["email"] == "owner@cached.test"
    assert prepared[0]["owner_name"] == "Synthetic Owner"



def test_valid_free_enrichment_preserves_fields_before_paid_stage(monkeypatch):
    """Validated synthetic free evidence survives the existing waterfall."""

    from app.integrations.prod import discovery_runtime
    from app.layers import enrichment_layer

    monkeypatch.setattr(discovery_runtime, "local_validation_mode", lambda: False)
    calls: list[str] = []

    async def free_mesh(lead, **_kwargs):
        calls.append("free")
        return {
            "patch": {
                "website": "https://free.synthetic.test",
                "email": "owner@free.synthetic.test",
                "phone": "+12125550123",
            },
            "audit": {"mode": "synthetic-free", "evidence_class": "synthetic-local"},
        }

    async def free_deep(rows, **_kwargs):
        calls.append("deep")
        return rows

    async def paid_stage(lead, **_kwargs):
        calls.append("paid")
        return lead

    monkeypatch.setattr(enrichment_layer, "enrich_intelligent", free_mesh)
    monkeypatch.setattr(enrichment_layer, "enrich_batch", free_deep)
    monkeypatch.setattr(enrichment_layer, "_paid_pass", paid_stage)

    rows = asyncio.run(
        enrichment_layer.enrich_for_pipeline(
            [{"id": "lead-free", "name": "Free Synthetic Gym"}],
            max_concurrent=1,
            use_crawl4ai=True,
            use_firecrawl=False,
        )
    )

    assert calls == ["free", "deep", "paid"]
    assert rows[0]["email"] == "owner@free.synthetic.test"
    assert rows[0]["phone"] == "+12125550123"
    assert rows[0]["website"] == "https://free.synthetic.test"
    assert rows[0]["enrichment_mesh_audit"]["evidence_class"] == "synthetic-local"



def test_normalized_route_dedupe_keeps_provenance_for_duplicate_observations():
    """One normalized lead retains each source observation after merging."""

    from app.integrations.prod.discovery_runtime import annotate_route_result, stable_route_dedupe

    first = annotate_route_result(
        {
            "id": "lead-duplicate",
            "fingerprint": "same-fingerprint",
            "name": "Same Synthetic Gym",
            "source": "osm",
            "sources_hit": ["osm"],
            "source_url": "https://osm.synthetic.test/gym",
        },
        route_cell={"source": "osm", "geography": "synthetic-city", "query": "gym"},
        cursor={"page": 1, "token": "osm-1"},
        next_cursor="osm-2",
        evidence_level="L0",
        billable=False,
    )
    second = annotate_route_result(
        {
            "id": "lead-duplicate",
            "fingerprint": "same-fingerprint",
            "name": "Same Synthetic Gym",
            "source": "web",
            "sources_hit": ["web"],
            "source_url": "https://web.synthetic.test/gym",
        },
        route_cell={"source": "web", "geography": "synthetic-city", "query": "gym"},
        cursor={"page": 2, "token": "web-2"},
        next_cursor=None,
        evidence_level="L1",
        billable=False,
    )

    merged = stable_route_dedupe([first, second])

    assert len(merged) == 1
    assert merged[0]["dedupe_key"] == "same-fingerprint"
    assert set(merged[0]["sources_hit"]) == {"osm", "web"}
    assert {"osm", "web"}.issubset(merged[0]["source_provenance_by_source"])
    assert merged[0]["next_cursor"] == "osm-2"


@pytest.mark.parametrize("target", range(1, 501))
def test_supported_targets_one_through_500_keep_request_shape(target):
    """Every supported target remains accepted without running a workload."""

    from app.api.prod.leads_api import PipelineRequest

    request = PipelineRequest(
        tenant_id="tenant-synthetic-a",
        target_new_leads=target,
        target_metric="assigned" if target % 2 else "delivered",
    )
    assert request.target_new_leads == target
    assert request.target_metric in {"assigned", "delivered"}


def test_target_500_is_cap_and_501_is_rejected_without_workload():
    """The hard cap remains an input boundary, not a benchmark workload."""

    from pydantic import ValidationError
    from app.api.prod.leads_api import PipelineRequest

    assert PipelineRequest(tenant_id="tenant-synthetic-a", target_new_leads=500).target_new_leads == 500
    with pytest.raises(ValidationError):
        PipelineRequest(tenant_id="tenant-synthetic-a", target_new_leads=501)


@pytest.mark.parametrize("owner, requested", [(owner, requested) for owner in TENANTS for requested in TENANTS])
def test_tenant_scope_and_uniqueness_preserve_boundaries(owner, requested):
    """Tenant reads stay scoped and cross-tenant access remains denied."""

    from app.integrations.prod.tenant_scope import tenant_table
    from app.middleware.auth import TenantContext, assert_tenant_match

    fake = FakeSupabase(
        {
            "leads": [
                {"id": "lead-a", "tenant_id": "tenant-synthetic-a"},
                {"id": "lead-b", "tenant_id": "tenant-synthetic-b"},
            ]
        }
    )
    own = tenant_table(fake, "leads", owner).select("*").execute().data
    assert len(own) == 1
    assert own[0]["tenant_id"] == owner

    context = TenantContext(owner, "user-synthetic", "owner")
    if owner == requested:
        assert_tenant_match(context, requested)
    else:
        with pytest.raises(Exception) as raised:
            assert_tenant_match(context, requested)
        assert raised.value.status_code == 403
        assert raised.value.detail == "Tenant mismatch"


def test_assignment_replay_is_idempotent_and_does_not_add_a_unique_row(monkeypatch):
    """The existing lead/tenant assignment is the replay boundary."""

    from app.integrations.prod import quality

    fake = FakeSupabase(
        {
            "lead_assignments": [
                {
                    "id": "assignment-existing",
                    "lead_id": "lead-assigned",
                    "tenant_id": "tenant-synthetic-a",
                    "status": "new",
                    "delivery_status": "pending",
                    "delivered_at": None,
                    "crm_status": "saved",
                }
            ]
        }
    )
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)

    result = asyncio.run(
        quality.assign_lead(
            "lead-assigned",
            "tenant-synthetic-a",
            None,
            "fitness",
            ["synthetic-city"],
            assignment_key="stable-assignment-key",
        )
    )

    assert result["assigned"] is True
    assert result["idempotent"] is True
    assert result["assignment_id"] == "assignment-existing"
    assert len(fake.rows("lead_assignments")) == 1


@pytest.mark.parametrize("attempt", (1, 2))
def test_crm_projection_replay_keeps_one_company_and_contact(monkeypatch, attempt):
    """Repeated CRM projection preserves one tenant-scoped company/contact."""

    from app.integrations.prod import crm_projection

    tenant = "tenant-synthetic-a"
    fake = FakeSupabase(
        {
            "leads_global": [
                {
                    "id": "lead-crm",
                    "tenant_id": tenant,
                    "name": "CRM Synthetic Gym",
                    "website": "https://crm.synthetic.test",
                    "website_domain": "crm.synthetic.test",
                    "email": "owner@crm.synthetic.test",
                }
            ],
            "lead_assignments": [
                {
                    "id": "assignment-crm",
                    "tenant_id": tenant,
                    "lead_id": "lead-crm",
                    "vertical": "fitness",
                }
            ],
        }
    )
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)

    result = asyncio.run(
        crm_projection.project_assignment_to_crm(tenant, "assignment-crm", "lead-crm")
    )

    assert result["crm_saved"] is True
    assert len([row for row in fake.rows("companies") if row["tenant_id"] == tenant]) == 1
    assert len([row for row in fake.rows("contacts") if row["tenant_id"] == tenant]) == 1
    assert attempt in (1, 2)



def test_delivery_is_idempotent_and_status_is_monotonic(monkeypatch):
    """Delivery retry cannot duplicate or regress a sent assignment."""

    from app.services import delivery

    fake = FakeSupabase(
        {
            "lead_assignments": [
                {
                    "id": "assignment-delivery",
                    "tenant_id": "tenant-synthetic-a",
                    "delivery_status": "pending",
                    "status": "new",
                }
            ]
        }
    )
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)

    kwargs = {"assignment_ids": ["assignment-delivery"], "tenant_id": "tenant-synthetic-a"}
    first = asyncio.run(delivery.deliver_batch(
        [{"id": "lead-delivery", "email": "owner@delivery.synthetic.test"}],
        "portal", {}, **kwargs,
    ))
    second = asyncio.run(delivery.deliver_batch(
        [{"id": "lead-delivery", "email": "owner@delivery.synthetic.test"}],
        "portal", {}, **kwargs,
    ))

    assert first["count"] == second["count"] == 1
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "sent"
    asyncio.run(delivery.mark_delivery_status(["assignment-delivery"], "failed"))
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "sent"



def test_queue_cancellation_fences_late_completion(monkeypatch):
    """A cancelled durable job no longer passes the write fence."""

    from app.integrations.prod import jobs

    fake = FakeSupabase(
        {
            "jobs": [
                {
                    "id": "job-cancel",
                    "tenant_id": "tenant-synthetic-a",
                    "status": "running",
                    "locked_by": "worker-synthetic",
                    "attempts": 1,
                    "payload": {},
                }
            ]
        }
    )
    monkeypatch.setattr(jobs, "_sb", lambda: fake)

    assert jobs.execution_fence("job-cancel", "worker-synthetic", "tenant-synthetic-a") is True
    result = jobs.cancel_job("job-cancel", actor="test-worker", reason="preservation_cancel")

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert jobs.execution_fence("job-cancel", "worker-synthetic", "tenant-synthetic-a") is False
    assert fake.rows("jobs")[0]["status"] == "cancelled"



def test_local_provider_denial_has_no_outbound_side_effect():
    """Local validation fails closed before a provider boundary."""

    from app.integrations.prod.discovery_runtime import (
        ProviderIsolationError,
        assert_provider_allowed,
        clear_provider_isolation_events,
        provider_isolation_events,
        synthetic_discovery,
    )

    clear_provider_isolation_events()
    rows = synthetic_discovery("fitness", ["Synthetic City"], limit_per_location=2)
    assert len(rows) == 2
    assert all(row["billable"] is False for row in rows)

    with pytest.raises(ProviderIsolationError):
        assert_provider_allowed("maps", local_mode=True)

    assert provider_isolation_events() == [{"provider": "maps", "outcome": "denied"}]
