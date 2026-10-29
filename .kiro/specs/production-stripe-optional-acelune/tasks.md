# Implementation Plan

## Overview

This plan makes Stripe optional for hardened startup while preserving safe billing failures, Acelune URL/CORS separation, local-only secret guidance, tenant protections, non-billing behavior, and configured-Stripe behavior. It is intentionally limited to focused tests, one runtime fix, documentation, and validation; it does not implement code in this task-creation step.

All paths below are relative to `sqlfix_latest/`. Tasks are dependency-ordered and use synthetic configuration only. Do not read, print, validate, rewrite, or modify `sqlfix_latest/.env`.

## Scope and dependency order

- **Runtime implementation:** `sqlfix_latest/app/core/runtime_config.py`
- **Existing runtime tests:** `sqlfix_latest/tests/test_runtime_config.py`
- **Focused regression tests:** `sqlfix_latest/tests/test_production_stripe_optional_acelune.py` (new)
- **Documentation/config template:** `sqlfix_latest/.env.example`, `sqlfix_latest/SETUP.md`
- **Billing behavior:** verify the existing guards in `sqlfix_latest/app/api/prod/billing_api.py`; change that file only if a focused test proves the existing safe `503` boundary is missing.
- **Out of scope:** `.env`, migrations, frontend changes, live Stripe/Supabase calls, production deployment, DNS propagation, and broad billing-route redesign.

## Task Dependency Graph

```json
{
  "waves": [
    {"id": 0, "tasks": ["1"]},
    {"id": 1, "tasks": ["2"]},
    {"id": 2, "tasks": ["3.1", "3.2"]},
    {"id": 3, "tasks": ["3.3"]},
    {"id": 4, "tasks": ["3.4"]},
    {"id": 5, "tasks": ["4"]}
  ]
}
```

## Tasks

- [x] 1. Write the bug-condition exploration test before the fix
  - **Property 1: Bug Condition** - Stripe-Optional Hardened Startup and Safe Billing
  - **IMPORTANT:** Write and run this test on the unfixed code; the initial failure is expected and documents the defect.
  - **File scope:** `sqlfix_latest/tests/test_production_stripe_optional_acelune.py` only (new).
  - **Dependencies:** None.
  - Build otherwise-valid synthetic `production`, `prod`, and `staging` environments with both `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` absent. Assert the expected behavior from the design: runtime validation accepts non-billing configuration, and missing-Stripe checkout/webhook paths return the existing generic unavailable response (`503`) without a traceback, fabricated URL/key, or secret-value leakage.
  - Scope the generated cases to the hardened environments and concrete missing-Stripe inputs so the unfixed startup failure is reproducible. Record the counterexample showing Stripe is incorrectly reported as a required startup key. Exercise existing billing guards through mocks/fakes; do not make provider calls.
  - **Expected unfixed outcome:** startup assertion fails because Stripe is included in the hardened required-key calculation; the test must not be weakened to pass.
  - **Verification:** Document the observed counterexample, then keep the exact test for task 3.4 to prove the fix.
  - _Bug_Condition: `isBugCondition(input)` from the design when hardened startup is rejected only because Stripe is absent or a missing-Stripe billing operation is not safely unavailable._
  - _Expected_Behavior: `expectedBehavior(input, result)` from the design._
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.5_

- [x] 2. Write preservation property tests before implementation
  - **Property 2: Preservation** - Existing Product and Configured-Stripe Behavior
  - **IMPORTANT:** Follow observation-first methodology and verify these tests pass on the unfixed code.
  - **File scope:** `sqlfix_latest/tests/test_production_stripe_optional_acelune.py`; extend `sqlfix_latest/tests/test_runtime_config.py` only where an existing runtime fixture is the correct reuse point.
  - **Dependencies:** Task 1 may provide synthetic-environment helpers, but this remains a standalone pre-fix task.
  - Capture behavior outside the bug condition: development/local runtime defaults, invalid plan/interval validation, authentication and tenant-admin dependencies on billing routes, `/billing/status` boolean-only availability, non-billing CRM/pipeline/email access, and mocked valid-Stripe checkout/webhook paths. Assert that no secret values are returned or logged by these checks.
  - Reuse existing focused runtime tests rather than duplicating broad application fixtures. Observe and record the current behavior before changing production code.
  - **Verification:** Preservation tests pass on unfixed code and define the baseline task 3.4 must rerun unchanged.
  - _Non-bug condition: `NOT isBugCondition(input)`, including valid Stripe configuration and all non-billing/authenticated behavior._
  - _Preservation: Preservation Requirements from the design._
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Apply the minimal Stripe-optional fix and documentation
  - **Dependencies:** Tasks 1 and 2 must be complete; the exploration failure and preservation baseline must be recorded before implementation.
  - _Bug_Condition: hardened startup treats absent Stripe keys as fatal, or optional billing does not retain its safe unavailable boundary._
  - _Expected_Behavior: hardened non-billing startup succeeds without Stripe; missing-Stripe billing remains explicitly unavailable without exposing secrets._
  - _Preservation: authentication, tenant isolation, non-billing behavior, valid-Stripe behavior, and deny-by-default CORS remain unchanged._
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3_

  - [x] 3.1 Remove Stripe from hardened startup requirements
    - **File scope:** `sqlfix_latest/app/core/runtime_config.py` only.
    - Remove `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from `PRODUCTION_REQUIRED` and any duplicate hardened missing-key calculation. Do not add fake defaults, expose values in `RuntimePolicy`, or loosen unrelated non-Stripe validation.
    - Keep the existing optional-provider evaluation in billing routes. Inspect `sqlfix_latest/app/api/prod/billing_api.py` only to confirm `_stripe()`, checkout, status, and webhook guards retain the generic `503`/boolean contract; edit it only if the focused tests identify a missing guard.
    - **Verification:** Synthetic hardened configuration with Stripe absent reaches a usable runtime policy; unrelated required keys and URL/CORS validation remain enforced.

  - [x] 3.2 Document Acelune and optional Stripe configuration without secrets
    - **File scope:** `sqlfix_latest/.env.example` and `sqlfix_latest/SETUP.md`.
    - Document `PUBLIC_API_URL=https://api.acelune.com` separately from `SUPABASE_URL`, and document explicit production browser origins `https://acelune.com,https://www.acelune.com` through `CORS_ORIGINS`; keep `APP_PUBLIC_URL` as the portal/app URL and reject wildcard production CORS through existing behavior.
    - Mark Stripe secret and price settings optional until payments are enabled; state that absent Stripe disables only billing/checkout and does not block the rest of the product.
    - Describe local generation of internal secrets using a local command/secret manager and an ignored file. Never include real values, fake Stripe keys, endpoint-returned secrets, startup-log output, or `.env` contents.
    - **Verification:** Documentation contains only placeholders/examples and clearly separates API, app, and Supabase URLs; no secret-bearing file is read or changed.

  - [x] 3.3 Add focused billing, runtime, and Acelune configuration tests
    - **File scope:** `sqlfix_latest/tests/test_runtime_config.py` and `sqlfix_latest/tests/test_production_stripe_optional_acelune.py`.
    - Add focused runtime assertions for Stripe-free `production`/`staging` policy acceptance and for neither Stripe key appearing in missing-key diagnostics.
    - Add mocked billing assertions for missing-secret checkout/webhook `503` responses, tenant-scoped `/billing/status`, no secret leakage, invalid-plan `400` preservation, and valid-Stripe route reachability without live provider calls.
    - Add synthetic configuration assertions that Acelune `PUBLIC_API_URL` and the two explicit browser origins are accepted independently from `SUPABASE_URL`, while production wildcard/unlisted origins are not broadened.
    - Add safe documentation/helper assertions only if needed; test generation through output/file-path properties, never by printing or asserting a real secret.
    - **Verification:** Tests are deterministic, offline, and use mocks/synthetic values only.

  - [x] 3.4 Verify the original properties after implementation
    - **Property 1: Expected Behavior** - Re-run the exact exploration test from task 1; do not write a replacement. It must pass for every scoped hardened environment and missing-Stripe billing case.
    - **Property 2: Preservation** - Re-run the exact preservation tests from task 2; do not alter them to hide regressions. Existing runtime and billing behavior outside missing-Stripe startup must remain unchanged.
    - **Dependencies:** Tasks 3.1, 3.2, and 3.3.
    - **Verification:** Property 1 passes, Property 2 passes, no live provider/database/network service is contacted, and no secret value appears in test output.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3_

- [x] 4. Final focused validation checkpoint
  - **File scope:** No source changes; test output and task validation only.
  - **Dependencies:** Task 3.4.
  - From `sqlfix_latest/`, run:
    - `python -m pytest tests/test_production_stripe_optional_acelune.py tests/test_runtime_config.py`
    - `python -m pytest tests/test_billing_quota_15_2.py` (preservation check for existing billing behavior where applicable).
  - Review the diff for only the files named in this plan and run `git diff --check` on those non-secret files. Do not run live Stripe/Supabase calls, migrations, Docker deployment, DNS checks, frontend builds, or any command that reads `.env`.
  - **Verification criteria:** all focused tests pass; Stripe-free hardened startup succeeds; missing-Stripe billing is safely unavailable; Acelune API/app/Supabase values remain distinct; auth/tenant checks and valid-Stripe behavior are preserved; documentation contains no credentials; `.env` remains untouched.
  - _Requirements: 2.1–2.5, 3.1–3.3_

## Completion criteria

The bugfix task plan is complete when the pre-fix exploration failure and preservation baseline are documented, the runtime requirement is narrowed only for Stripe, Acelune/optional-Stripe guidance is documented without secret access, focused billing/runtime tests pass, both properties pass after the fix, and final validation is clean.

## Notes

- This is a task plan only; it does not implement the runtime, billing, or documentation changes.
- The exploration test is expected to fail before implementation and must be rerun unchanged after the fix.
- Use synthetic values and mocks only. Never inspect, print, validate, rewrite, or modify `.env` or perform live provider/deployment checks.
