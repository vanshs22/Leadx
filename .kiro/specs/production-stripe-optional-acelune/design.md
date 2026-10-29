# Production Stripe Optional Acelune Bugfix Design

## Overview

Production startup currently treats Stripe keys as mandatory even when billing is not enabled. The minimal fix removes Stripe from hardened-environment startup requirements, keeps billing routes fail-safe through their existing `503` contract, documents the Acelune public URL/CORS values separately from Supabase configuration, and provides local-only internal-secret generation guidance. Authentication, tenant authorization, CRM/pipeline/email behavior, and configured-Stripe behavior remain unchanged.

## Glossary

- **Bug_Condition (C)**: A hardened runtime has no Stripe secrets, but startup rejects the configuration or a billing request crashes instead of returning a safe disabled response.
- **Property (P)**: Stripe-free production starts and Stripe-dependent endpoints report an explicit unavailable state without leaking configuration values.
- **Preservation**: Existing auth, tenant isolation, non-billing product behavior, and valid-Stripe behavior remain unchanged.
- **Hardened environment**: `production`, `prod`, or `staging` as recognized by `app/core/runtime_config.py`.
- **Stripe-disabled**: `STRIPE_SECRET_KEY` is absent, so checkout cannot be created; the webhook remains unavailable when its signing secret is absent.
- **Public API URL**: The externally reachable API base, configured with `PUBLIC_API_URL`; it is distinct from `SUPABASE_URL` and the portal/app URL.

## Bug Details

### Bug Condition

The defect occurs when a hardened process is configured without Stripe because payments are intentionally disabled. `validate_runtime_configuration()` currently includes Stripe keys in the production-required set, so unrelated API/worker startup can be refused. Billing and webhook requests must also remain safe when their optional provider configuration is absent.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input containing environment, Stripe configuration, and optional request
  OUTPUT: boolean

  stripeDisabled := input.environment IN {"production", "prod", "staging"}
                    AND input.stripeSecretKey is empty
                    AND input.stripeWebhookSecret is empty

  startupFailure := input.operation = "startup"
                    AND startupRejectsOnlyBecauseOfStripe(input)

  unsafeBillingFailure := input.operation IN {"checkout", "billing-webhook"}
                          AND stripeDisabled
                          AND responseIsNotSafeUnavailable(input)

  RETURN stripeDisabled AND (startupFailure OR unsafeBillingFailure)
END FUNCTION
```

### Examples

- A production API with valid Supabase, Redis, service, delivery, unsubscribe, OAuth, and CORS configuration but empty Stripe keys currently fails validation; it should start with billing disabled.
- An authenticated tenant administrator requests `/billing/create-checkout-session` for a paid plan without `STRIPE_SECRET_KEY`; it should receive the existing HTTP `503` message rather than a traceback or fabricated checkout URL.
- A Stripe webhook request without `STRIPE_WEBHOOK_SECRET` should retain its existing HTTP `503` unavailable response.
- A production Acelune deployment should be able to configure `PUBLIC_API_URL=https://api.acelune.com` and CORS origins for `https://acelune.com` and `https://www.acelune.com` without treating `SUPABASE_URL` as the public API URL.
- A valid Stripe deployment with real secret and price configuration should continue to create checkout sessions and process webhooks under the current auth and tenant checks.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Authentication, admin/tenant authorization, CSRF protections, and tenant isolation remain enforced on billing and all other tenant-scoped routes.
- CRM, pipeline, enrichment, delivery, and email functionality do not depend on Stripe being configured and continue to operate as before.
- `/billing/status` continues to require tenant access and reports Stripe availability without exposing secret values.
- The existing `503` unavailable contract from `_stripe()`, missing Stripe prices, and missing webhook configuration is preferred; no broad 404 route redesign is required.
- When valid Stripe secrets and price IDs are configured, checkout creation, webhook signature validation, idempotency, and subscription state transitions remain unchanged.
- Production CORS remains deny-by-default for unspecified/wildcard origins; only explicitly configured public Acelune origins are allowed.

**Scope:**

Inputs outside the missing-Stripe configuration condition must be unaffected. This includes valid-Stripe requests, invalid plan validation, authenticated non-billing requests, Supabase access, local/development defaults, and existing same-origin portal behavior.

## Hypothesized Root Cause

1. **Stripe incorrectly classified as mandatory**: `PRODUCTION_REQUIRED` in `app/core/runtime_config.py` includes `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, and the legacy missing-key check repeats that requirement.
2. **Optional-provider state is route-local**: Billing already has safe guards in `billing_api.py`, but startup validation prevents the rest of the service from reaching a usable Stripe-disabled state.
3. **Public URL responsibilities are easy to conflate**: Existing runtime settings separately use `PUBLIC_API_URL`, `APP_PUBLIC_URL`, and `SUPABASE_URL`, but deployment guidance needs to state that Acelune API/app origins and Supabase are different values.
4. **Secret setup guidance can leak or encourage placeholders**: Production setup documentation currently describes secrets alongside provider configuration; local generation must remain operator-local and must not suggest fake Stripe credentials or emit values through the API/logs.

## Correctness Properties

Property 1: Bug Condition - Stripe-Optional Hardened Startup and Safe Billing

_For any_ hardened runtime input where Stripe secrets are empty, the fixed runtime validator SHALL accept otherwise-valid non-billing configuration, and Stripe-dependent checkout/webhook operations SHALL return the existing safe unavailable response without crashing, inventing keys, or exposing secret values.

```text
FUNCTION expectedBehavior(input, result)
  IF isBugCondition(input) THEN
    RETURN input.operation = "startup"
           IMPLIES result.startedSuccessfully
           AND input.operation IN {"checkout", "billing-webhook"}
           IMPLIES result.httpStatus = 503
           AND result.containsNoSecretValue
  END IF
END FUNCTION
```

**Validates: Requirements 2.1, 2.2, 2.4, 2.5**

Property 2: Preservation - Existing Product and Configured-Stripe Behavior

_For any_ input where the bug condition does NOT hold, the fixed code SHALL produce the same established behavior as the original code, preserving authentication, tenant isolation, non-billing CRM/pipeline/email behavior, and valid-Stripe checkout/webhook behavior.

**Validates: Requirements 3.1, 3.2, 3.3**

Property 3: Acelune Public Configuration Separation

_For any_ valid Acelune deployment configuration, `PUBLIC_API_URL` SHALL identify the API origin, `CORS_ORIGINS` SHALL allow only explicitly listed Acelune browser origins, and `SUPABASE_URL` SHALL remain an independent Supabase configuration value.

**Validates: Requirements 2.3, 3.1**

## Fix Implementation

### Changes Required

**File**: `sqlfix_latest/app/core/runtime_config.py`

1. Remove `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from `PRODUCTION_REQUIRED` and from any duplicate hardened-environment missing-key calculation.
2. Do not add fake defaults or include secret values in `RuntimePolicy`, diagnostics, or startup errors. Stripe remains optional and is evaluated by the billing routes when used.
3. Keep all existing non-Stripe hardened requirements and URL validation intact.

**File**: `sqlfix_latest/app/api/prod/billing_api.py`

1. Preserve `_stripe()` and webhook guards as the single safe unavailable boundary: missing Stripe configuration returns HTTP `503` with a generic message.
2. Keep `/billing/status` tenant-scoped and use its existing boolean availability signal; do not return keys, prices, or provider errors.
3. Do not introduce a 404 API contract unless an existing frontend route demonstrably requires it. If the checkout UI needs a narrow disabled state, map only the known `503` response to a clear “payments not configured” message and leave other errors unchanged.
4. Leave valid-Stripe price resolution, checkout creation, webhook signature validation, and event processing unchanged.

**Files**: `sqlfix_latest/.env.example`, `sqlfix_latest/SETUP.md`, and deployment documentation as needed

1. Document `PUBLIC_API_URL=https://api.acelune.com` separately from `SUPABASE_URL`.
2. Document `CORS_ORIGINS=https://acelune.com,https://www.acelune.com` as explicit browser origins; do not use `*` in production. Keep `APP_PUBLIC_URL` as the portal/app URL rather than treating it as the API URL.
3. Mark Stripe keys and Stripe price IDs as optional until payments are enabled. State that absent Stripe configuration disables only billing/checkout.
4. Describe local generation of internal secrets using a local secret manager or a local command redirected to an ignored file. Values must never be pasted into chat, committed, returned by an endpoint, or written to startup logs. Do not recommend fake Stripe keys.
5. Do not inspect, rewrite, or include the contents of the existing `.env` file.

## Testing Strategy

### Validation Approach

Use focused regression tests around runtime policy, billing route guards, and public-origin configuration. Run tests with synthetic values only; no live Stripe, Supabase, or secret values are required.

### Exploratory Bug Condition Checking

Before the fix, add or run a focused test with an otherwise-valid hardened environment and both Stripe keys empty. The expected counterexample is startup refusal naming the missing Stripe configuration. Also exercise the existing billing guards with missing keys to confirm their intended `503` contract rather than changing that contract unnecessarily.

### Fix Checking

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedRuntimeOrBillingOperation(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

Targeted examples:

- `load_runtime_policy()` accepts a production/staging environment with Stripe keys absent when all unrelated hardened requirements are satisfied.
- `validate_runtime_configuration()` does not list either Stripe key as missing.
- `_stripe()` returns HTTP `503` without importing/initializing Stripe when its secret is absent.
- Checkout and webhook requests remain safe and do not expose environment values.
- Acelune API and browser origins are accepted as explicit configuration, while a wildcard is rejected/removed by existing production CORS behavior.

### Preservation Checking

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalBehavior(input) = fixedBehavior(input)
END FOR
```

Verify that authentication and tenant dependencies remain on billing routes, non-billing routes remain available, invalid plan/interval requests still return their existing `400`, and valid Stripe configuration still reaches the current checkout/webhook code paths.

### Unit Tests

- Extend `sqlfix_latest/tests/test_runtime_config.py` to cover Stripe-free production and staging validation and assert that Stripe keys are not reported as missing.
- Add focused billing tests (for example `tests/test_production_stripe_optional_acelune.py`) for missing-secret `503` responses, tenant-scoped status, and no secret-value leakage.
- Test explicit Acelune `PUBLIC_API_URL`/`CORS_ORIGINS` configuration and separation from `SUPABASE_URL` without reading `.env`.
- Test local secret-generation guidance or helper behavior only through safe output/file assertions; never assert or print a real secret.

### Property-Based Tests

- Generate hardened environments with optional Stripe keys absent or present and assert that only the absence case changes billing availability, not unrelated runtime policy validation.
- Generate non-billing requests and configured-Stripe inputs to preserve existing authorization and route behavior.
- Generate allowed-origin lists and assert production CORS never broadens to `*` and does not substitute the Supabase origin for the public API/app origins.

### Integration Tests

- Boot the API runtime with synthetic production configuration and no Stripe keys; assert readiness/startup succeeds and billing checkout returns `503`.
- Boot with synthetic valid Stripe configuration through mocks/fakes, assert existing checkout/webhook contracts remain reachable, and verify tenant authorization is still enforced.
- Exercise an Acelune origin against CORS preflight and confirm the two browser origins are allowed while an unlisted origin is not.

Live provider calls, production deployment, actual DNS propagation, and the contents of `.env` are outside this targeted design validation.
