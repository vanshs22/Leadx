# Bugfix Requirements Document

## Introduction

LeadX is not ready for public paid production because the working-code release contains security, operational, billing, deployment, reproducibility, schema, and runtime-readiness defects. This bugfix scope covers the blockers required to establish a safe production-readiness decision for tenant isolation, CI/UAT evidence, billing, deployment defaults, provider integrations, support operations, dependency and image reproducibility, migrations, and lead delivery.

The acceptance decision SHALL distinguish static inspection, local execution, UAT execution, and live-environment verification. No result from one evidence class SHALL be presented as proof of another. This document does not authorize reading or exposing secrets, destructive live database changes, unapproved provider actions, deployment, or claims that Supabase, RLS, realtime, Stripe, or external providers have been verified live without recorded evidence from the relevant environment.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a specialized agent is given lead identifiers that are not assigned to its tenant or authorized mission THEN the system accepts those identifiers without proving tenant ownership and mission authorization, creating a cross-tenant data exposure risk.

1.2 WHEN the CI or UAT workflow is run against its declared checks THEN the workflow can fail to collect tests or reference missing scripts and end-to-end directories, so a passing-looking path does not establish meaningful validation.

1.3 WHEN a customer needs to cancel a subscription or manage billing THEN the product does not provide a complete customer-facing cancellation or billing-portal path, and displayed payment state can be misleading because a customer identifier is treated as evidence of a payment method.

1.4 WHEN the default Compose deployment is started without production hardening overrides THEN Redis, API, portal, or scraper services can be exposed beyond the intended trust boundary and runtime settings can default to development or insecure behavior.

1.5 WHEN the release is assessed for production integrations THEN live Supabase schema/RLS/realtime behavior, Stripe behavior, and provider behavior remain unverified or are represented without environment-specific evidence.

1.6 WHEN a customer or operator uses the support conversation surface THEN the surface does not provide a formal ticket lifecycle with clear ownership, status transitions, and closure semantics.

1.7 WHEN the application is built or deployed from dependency and container definitions THEN dependency versions, image provenance, or lock/hash information can be insufficient to reproduce the assessed artifact reliably.

1.8 WHEN migrations are applied to an environment whose schema or data differs from the assumed baseline THEN migration and schema drift can cause duplicate-policy failures, incompatible constraints, or unsafe changes without a verified compatibility path.

1.9 WHEN discovery, qualification, assignment, delivery, or runtime recovery is exercised THEN reported counts and states can diverge, leads can pass a gate without assignment, runs can remain stuck, and delivery can fail to reconcile assigned, reserved, and delivered records.

1.10 WHEN pricing or plan limits are read by billing or product surfaces THEN hard-coded or duplicated pricing definitions can drift, causing inconsistent amounts, entitlements, or customer-facing plan information.

1.11 WHEN production-readiness evidence is reviewed THEN static, local, UAT, and live observations can be mixed together, allowing an unverified live claim or a local result to be treated as release approval.

1.12 WHEN remediation is prepared for a live environment THEN the release process lacks an explicit safety boundary that prevents secret exposure, destructive schema/data operations, unapproved provider charges, and deployment before required gates pass.

### Expected Behavior (Correct)

2.1 WHEN a specialized agent receives any lead identifier list THEN the system SHALL authorize every lead against the requesting tenant, the active mission, and the applicable assignment policy before reading or returning it, and SHALL reject unauthorized identifiers without revealing their records.

2.2 WHEN CI or UAT workflows run THEN the system SHALL execute only existing, discoverable checks, report the exact collected-test result, fail clearly when a required check is missing, and provide reproducible artifacts for each declared gate.

2.3 WHEN a customer manages billing THEN the product SHALL provide an authenticated cancellation and customer-portal path, SHALL show billing state from authoritative payment/subscription state, SHALL distinguish a customer record from a payment method, and SHALL show actionable failure or pending states rather than implying successful payment setup.

2.4 WHEN the default Compose deployment is used THEN the system SHALL bind services only to intended interfaces, keep internal services private, require production-safe runtime settings for production use, and make any development-only exposure explicit rather than silently enabled.

2.5 WHEN production readiness is reported for an integration THEN the report SHALL identify the environment, test date, scope, result, and limitations for Supabase schema/RLS/realtime, Stripe, and each required external provider; absent live evidence SHALL remain explicitly unverified.

2.6 WHEN a support conversation is created or updated THEN the system SHALL expose a formal ticket lifecycle with a stable identifier, tenant-safe access, ownership, status transitions, timestamps, and a clear resolved/closed outcome that can be audited.

2.7 WHEN the release is built or deployed THEN the system SHALL use pinned and reproducible dependency and image inputs with recorded lock/hash/provenance evidence sufficient to identify the assessed artifact and detect unexpected changes.

2.8 WHEN a migration is proposed or applied THEN the system SHALL compare it with the target schema and migration history, be safe to rerun where the operation requires it, handle existing data explicitly, preserve tenant/security invariants, and require a reviewed backup and rollback or recovery plan before any live change.

2.9 WHEN discovery through delivery runs THEN the system SHALL enforce valid state transitions, stop at the requested target, prevent duplicate or unauthorized assignment, reconcile discovered, qualified, assigned, reserved, and delivered counts, recover or surface orphaned runs, and expose failures with actionable status rather than leaving misleading running states.

2.10 WHEN plan pricing, billing, or entitlements are displayed or enforced THEN the system SHALL use one authoritative versioned source, keep customer-facing and enforcement values consistent, and identify any pending or migrated pricing state explicitly.

2.11 WHEN readiness evidence is recorded THEN the system SHALL keep static analysis, local execution, UAT execution, and live verification in separate labeled evidence classes, and SHALL permit production approval only when every required gate has evidence from the required class.

2.12 WHEN any change is proposed for a live environment THEN the process SHALL block execution unless secrets remain outside source and logs, the change is non-destructive or explicitly approved, provider cost and side effects are bounded, rollback/recovery is defined, and all prerequisite production-readiness gates have passed.

2.13 WHEN the production-readiness review is conducted THEN the review SHALL pass through phased gates in order: static inventory and threat review; reproducible local build and focused tests; isolated UAT with representative data and integration contracts; controlled live verification with authorized credentials and non-destructive checks; and final release approval with monitoring, rollback, ownership, and incident-response evidence.

2.14 WHEN any phased gate fails, lacks required evidence, or contains an unresolved high-severity security, tenant-isolation, data-integrity, billing, or deployment defect THEN the system SHALL mark the release NO-GO for public paid production and SHALL identify the blocker and the evidence needed to reopen the gate.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN an authorized tenant requests its own leads through an authorized mission THEN the system SHALL CONTINUE TO return only the leads permitted by the existing assignment and tenant policy, without reducing valid access.

3.2 WHEN CI, local, or UAT checks are valid and present THEN the system SHALL CONTINUE TO run them using their existing supported commands and report their actual results without silently broadening scope.

3.3 WHEN a customer has a valid subscription, payment method, or cancellation request THEN the system SHALL CONTINUE TO preserve the provider's authoritative state and SHALL not cancel, charge, or alter billing records merely because readiness evidence is being collected.

3.4 WHEN services communicate within the intended private deployment boundary THEN the system SHALL CONTINUE TO support the existing application, worker, portal, scraper, Redis, database, and provider workflows without requiring public exposure of internal services.

3.5 WHEN an integration is not required for a particular release gate or lacks authorized live credentials THEN the system SHALL CONTINUE TO operate in the supported local or UAT mode while labeling live behavior unverified instead of fabricating success.

3.6 WHEN a support ticket, migration, dependency artifact, or runtime event is valid and authorized THEN the system SHALL CONTINUE TO preserve its data, audit history, and tenant ownership while adding the required lifecycle or evidence controls.

3.7 WHEN a lead run completes successfully below, at, or above a previously supported target within configured limits THEN the system SHALL CONTINUE TO honor configured quality, quota, assignment, delivery, and stop conditions without wasting provider calls or changing unrelated plan behavior.

3.8 WHEN a production-readiness gate passes with complete evidence THEN the system SHALL CONTINUE TO allow the next approved phase, but SHALL not treat that phase as live verification or final public-production approval until its own criteria pass.
