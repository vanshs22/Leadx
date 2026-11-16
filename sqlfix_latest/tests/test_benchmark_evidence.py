"""Offline contract tests for benchmark methodology and evidence claims.

**Validates: Requirements 17.1-17.5, 18.1-18.5, 20.1-20.3**
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.orchestration.benchmarks import (
    BENCHMARK_REQUIRED_FIELDS,
    BenchmarkEvidenceKind,
    BenchmarkSpec,
    BenchmarkStatus,
    BenchmarkValidationError,
    assess_benchmark,
    validate_benchmark_spec,
)


def _spec(**overrides) -> BenchmarkSpec:
    values = {
        "capability": "bounded route planning",
        "population": "synthetic local route fixtures",
        "time_window": "2026-01-01/2026-01-31",
        "inputs": {"target": 5, "vertical": "gym", "geography": "New Delhi"},
        "methodology": "Replay the same fixture set with a fixed planner version.",
        "metrics": ("route_count", "unique_entities", "latency_ms"),
        "sample": {"size": 20, "unit": "requests"},
        "exclusions": ("live providers", "deployed services"),
        "comparator_version_plan": "planner-v1 vs planner-v0, same fixture plan",
        "stopping_rule": "Stop after 20 requests or the 5-lead target.",
        "evidence_class": "synthetic/offline",
        "evidence_kind": BenchmarkEvidenceKind.CONTROLLED_MEASUREMENT.value,
        "source": "offline fixture: benchmark/routes-v1",
        "date": "2026-02-01",
        "limitations": "Synthetic data does not establish provider or customer performance.",
        "confidence": 0.8,
        "comparability": {"comparable": True, "basis": "same inputs and plan"},
        "environment": "offline",
    }
    values.update(overrides)
    return BenchmarkSpec(**values)


def test_benchmark_requires_every_methodology_and_evidence_field():
    assert set(BENCHMARK_REQUIRED_FIELDS) >= {
        "capability", "population", "time_window", "inputs", "methodology", "metrics",
        "sample", "exclusions", "comparator_version_plan", "stopping_rule",
        "evidence_class", "source", "date", "limitations", "confidence",
        "comparability", "environment",
    }
    for field in BENCHMARK_REQUIRED_FIELDS:
        with pytest.raises(BenchmarkValidationError, match=field):
            validate_benchmark_spec(replace(_spec(), **{field: None}))


def test_evidence_kinds_remain_distinct_and_are_normalized_at_the_boundary():
    assert set(item.value for item in BenchmarkEvidenceKind) == {
        "documented capability", "controlled measurement", "customer report",
        "public observation", "unverified assertion",
    }
    assert validate_benchmark_spec(_spec(evidence_kind="controlled_measurement")).evidence_kind == "controlled measurement"


def test_static_local_and_synthetic_evidence_cannot_emit_restricted_claims():
    restricted = "LeadX is superior on coverage and provider success."
    for evidence_class in ("static", "local", "synthetic/offline"):
        result = assess_benchmark(_spec(evidence_class=evidence_class), claim=restricted)
        assert result.status == BenchmarkStatus.INSUFFICIENT_EVIDENCE.value
        assert result.claim is None


def test_public_assertion_cannot_emit_competitor_or_live_readiness_claim():
    result = assess_benchmark(
        _spec(evidence_kind="unverified assertion", evidence_class="static"),
        claim="The competitor is production ready.",
    )
    assert result.status == BenchmarkStatus.INSUFFICIENT_EVIDENCE.value
    assert result.claim is None


def test_missing_live_prerequisites_are_explicitly_blocked_or_not_tested():
    spec = _spec(evidence_class="live/provider")
    assert assess_benchmark(spec, prerequisites={"provider_access": False}).status == BenchmarkStatus.BLOCKED.value
    assert assess_benchmark(spec, prerequisites={"live_environment": False}).status == BenchmarkStatus.NOT_TESTED.value
    assert assess_benchmark(spec, prerequisites={"sample": False}).status == BenchmarkStatus.INSUFFICIENT_EVIDENCE.value


def test_comparable_controlled_live_measurement_is_the_only_restricted_pass():
    result = assess_benchmark(
        _spec(evidence_class="live/provider"),
        claim="LeadX has measured comparable provider success.",
        prerequisites={"provider_access": True, "live_environment": True},
    )
    assert result.status == BenchmarkStatus.PASS.value
    assert result.claim_emitted is True


def test_incomplete_mapping_emits_blocked_without_filling_claims():
    result = assess_benchmark({"capability": "documented capability"})
    assert result.status == BenchmarkStatus.BLOCKED.value
    assert result.claim is None


def test_benchmark_module_imports_without_runtime_or_secret_modules():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
from app.orchestration.benchmarks import BenchmarkSpec
blocked = {name for name in sys.modules if name.startswith(('boto3', 'supabase', 'redis', 'httpx', 'dotenv'))}
assert not blocked, blocked
print('offline-benchmark-import-ok')
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=root, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "offline-benchmark-import-ok"
