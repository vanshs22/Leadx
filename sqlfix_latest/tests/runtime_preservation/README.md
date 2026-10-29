# Healthy runtime preservation baseline

This directory records task 3's observation-first preservation property. It uses synthetic tenant/user/worker/job identities and in-memory Supabase/Redis doubles only; it does not open network connections, require credentials, invoke providers, or charge credits.

## Run

From `sqlfix_latest/`:

```bash
python -m pytest -q tests/runtime_preservation/test_healthy_runtime_property.py
```

The same test file is retained for post-fix regression validation. It is intentionally separate from `tests/runtime_exploration`, whose assertions are expected to fail against the unfixed bug-condition behavior.

## Healthy domain (`NOT isBugCondition`)

The generated matrix covers two synthetic tenants and ten existing workflows: client leads, pipeline, import, billing, settings plus admin health, jobs, tenants, usage, and security. Every generated case has healthy database/Redis/worker/queue dependencies, no duplicate-provider risk, no required Redis fallback, no loopback portal upstream, and bounded polling.

Observed baseline contracts:

- `GET /health/ready` returns HTTP 200 with `{ok: true, db: true, ms: number}` when the database double is healthy. This is the pre-fix response shape; the test does not assert any future readiness fields.
- Existing tenant helpers preserve tenant injection and reject a mismatched tenant with HTTP 403; cross-tenant synthetic rows remain disjoint.
- The registered handler set includes the current pipeline, schedule, CRM, email, CSV, reverify, agent, and webhook kinds. A synthetic handler probe confirms the existing `run_job` payload/tenant propagation and terminal result shape.
- Existing retry behavior remains bounded: a retryable failure returns to `queued` with `run_after`; an exhausted failure becomes `failed` with `finished_at` and clears the worker lock.
- Matching idempotency scope/request hashes produce one acquired claim followed by a replay of the same synthetic job result. Credit refund behavior is idempotent for the same assignment/type and creates one credit record.
- A healthy shared Redis double produces shared rate-limit enforcement and distinct tenant-scoped cache values across two simulated processes.
- Existing client and admin primary page files, both skip links, loading/error/empty/success state branches, visibility-aware polling cleanup, safe API error redaction, and finite portal response fixtures remain present.

## Baseline command results

Before adding this task's tests, the existing healthy baseline and route/auth contracts passed:

```text
python -m pytest -q tests/runtime_baseline tests/test_route_contract.py tests/test_admin_control_plane.py tests/test_leadx_12_governance.py
PASS: 13 passed, 1 skipped
```

The new property suite contains 15 deterministic generated/contract tests. The combined final run passes all selected tests with one existing environment-dependent skip:

```text
python -m pytest -q tests/runtime_preservation/test_healthy_runtime_property.py tests/runtime_baseline tests/test_route_contract.py tests/test_admin_control_plane.py tests/test_leadx_12_governance.py
PASS: 27 passed, 1 skipped
```

## Task 2 baseline evidence

The observation-first preservation cases pass against the current implementation before remediation:

```text
python -m pytest -q tests/runtime_preservation/test_lead_generation_preservation.py tests/runtime_preservation/test_healthy_runtime_property.py tests/test_route_contract.py tests/test_leadx16_free_first.py tests/test_pipeline_run_lifecycle.py tests/test_contact_quality.py tests/test_crm_projection_contract.py
......................s................................                             [100%]
PASS: 55 passed, 1 skipped
```

The skip is an existing environment-dependent case. The command uses synthetic/in-memory fixtures and makes no provider calls or real-run mutations.
