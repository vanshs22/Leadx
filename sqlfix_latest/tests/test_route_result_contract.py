"""Focused offline checks for the shared bounded discovery route contract."""
from __future__ import annotations

from app.integrations.prod.discovery_runtime import (
    ROUTE_CONTRACT_VERSION,
    annotate_route_result,
    route_failure,
    route_should_continue,
    stable_route_dedupe,
)


def test_route_rows_carry_bounded_page_budget_evidence_and_cost_metadata():
    row = annotate_route_result(
        {"id": "lead-1", "name": "Fixture Gym", "fingerprint": "fp-1", "source": "osm"},
        route_cell={
            "route_cell_id": "cell-1", "source": "osm", "source_family": "local",
            "sub_icp_id": "gym", "geography": "New Delhi", "query": "gym",
            "result_budget": 5,
        },
        page=1,
        cursor={"page": 1, "token": None},
        next_cursor={"page": 2, "token": "next"},
        requested_limit=5,
        evidence_level="L1",
        billable=False,
    )

    assert row["contract_version"] == ROUTE_CONTRACT_VERSION
    assert row["route_cell_id"] == "cell-1"
    assert row["page"] == 1 and row["requested_limit"] == 5
    assert row["next_cursor"] == {"page": 2, "token": "next"}
    assert row["evidence_level"] == "L1" and row["billable"] is False
    assert row["raw_hash"] and row["dedupe_key"] == "fp-1"
    assert row["source_provenance_by_source"]["osm"]["evidence_level"] == "L1"


def test_stable_route_dedupe_keeps_order_and_all_source_evidence():
    first = annotate_route_result(
        {"id": "lead-1", "name": "Shared Gym", "fingerprint": "fp-1", "source": "maps",
         "source_provenance": {"source": "maps", "evidence_level": "L1"}},
        source="maps", source_reference="maps:1", evidence_level="L1", billable=False,
    )
    second = annotate_route_result(
        {"id": "lead-1", "name": "Shared Gym", "fingerprint": "fp-1", "source": "web",
         "website": "https://shared.test", "source_provenance": {"source": "web", "evidence_level": "L2"}},
        source="web", source_reference="https://shared.test", evidence_level="L2", billable=True,
    )

    rows = stable_route_dedupe([first, second])
    assert len(rows) == 1
    assert rows[0]["source"] == "maps+web"
    assert rows[0]["sources_hit"] == ["maps", "web"]
    assert set(rows[0]["source_provenance_by_source"]) == {"maps", "web"}
    assert rows[0]["evidence_level"] == "L2"
    assert rows[0]["billable"] is True


def test_typed_failure_and_fencing_prevent_unbounded_next_page():
    failed = route_failure(
        route_cell={"route_cell_id": "cell-1", "source": "web", "result_budget": 5},
        page=2, cursor={"page": 2, "token": "same"},
        error=TimeoutError("fixture timeout"), fallback_reason="web_timeout",
    )

    assert len(failed) == 0
    assert failed.contract["typed_error"]["category"] == "timeout"
    assert failed.contract["fallback_reason"] == "web_timeout"
    assert route_should_continue(target=5, assignment_confirmed=5,
                                 cursor={"page": 1}, next_cursor={"page": 2}) is False
    assert route_should_continue(cancellation=lambda: True,
                                 cursor={"page": 1}, next_cursor={"page": 2}) is False
    assert route_should_continue(budget_remaining=0,
                                 cursor={"page": 1}, next_cursor={"page": 2}) is False
    assert route_should_continue(cursor={"page": 2}, next_cursor={"page": 2}) is False
