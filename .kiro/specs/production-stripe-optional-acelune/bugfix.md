# Bugfix Requirements Document

## Introduction

Production startup currently depends on Stripe configuration even when billing is intentionally disabled. This bugfix makes Stripe optional for production deployment while preserving safe billing behavior, correct Acelune runtime URLs, local secret handling, and all non-billing product capabilities.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are empty in a production environment THEN the system may fail to start instead of running with billing disabled.
1.2 WHEN billing or checkout surfaces are accessed while Stripe is not configured THEN the system may crash or expose an unsafe failure instead of returning the existing safe unavailable contract or a 404.
1.3 WHEN production is configured for Acelune THEN the runtime may lack supported non-secret values for `https://api.acelune.com`, `https://acelune.com`, and `https://www.acelune.com`, or may conflate the Supabase URL with `PUBLIC_API_URL`.
1.4 WHEN internal production secrets are needed THEN the setup may encourage exposing secret values or using fake Stripe credentials instead of generating internal secrets locally and keeping them private.

### Expected Behavior (Correct)

2.1 WHEN `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are empty in a production environment THEN the system SHALL start successfully with Stripe-dependent billing functionality disabled.
2.2 WHEN billing or checkout surfaces are accessed while Stripe is not configured THEN the system SHALL clearly indicate a disabled or not-configured state using HTTP 404 or the existing safe unavailable contract, without crashing.
2.3 WHEN production is configured for Acelune THEN non-secret environment values SHALL support `https://api.acelune.com`, `https://acelune.com`, and `https://www.acelune.com`, while the Supabase URL SHALL remain a separate configuration value from `PUBLIC_API_URL`.
2.4 WHEN internal secrets are required for production setup THEN the system SHALL support generating them locally without exposing their values.
2.5 WHEN Stripe is not configured THEN the system SHALL NOT require, invent, or accept fake Stripe keys as a substitute for real configuration.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN users authenticate or access tenant-scoped data THEN the system SHALL CONTINUE TO enforce existing authentication and tenant isolation behavior.
3.2 WHEN users use non-billing CRM, pipeline, or email functionality THEN the system SHALL CONTINUE TO operate as it did before this bugfix.
3.3 WHEN valid Stripe secrets are configured THEN the system SHALL CONTINUE TO expose existing billing and checkout behavior without changing its established contract.
