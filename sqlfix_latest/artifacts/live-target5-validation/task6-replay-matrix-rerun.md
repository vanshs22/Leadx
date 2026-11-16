# Task 6 Replay Matrix Rerun

- **Task:** `live-target5-validation.6`
- **Run status:** **BLOCKED**
- **Evidence class:** `local`
- **Run mode:** bounded disposable-scope preflight only
- **Created durable test scope:** none
- **Cleanup-required opaque IDs:** none

## Safety boundary

The local API, worker, Redis, and durable database boundary was checked before the test. The approved real tenant was not used. The pipeline discovery handler was not invoked. No provider, enrichment, assignment, delivery, email, outreach, campaign, Stripe, SES, migration, SQL mutation, retry, cancel, reset, cleanup, queue-clear, or credential operation was attempted.

## Runtime preflight

| Component | Result | Evidence |
|---|---|---|
| API TCP `127.0.0.1:8000` | **PASS** | Local port reachable |
| Portal TCP `127.0.0.1:3000` | **PASS** | Local port reachable |
| Redis TCP `127.0.0.1:6379` | **PASS** | Local port reachable |
| API `/health/live` | **PASS** | HTTP 200 |
| API `/health/ready` | **PASS** | HTTP 200; database, Redis, worker, queue, and provider-isolation checks reported ready |
| API `/health/routes` | **PASS** | HTTP 200 |

## Disposable scope admission

A tagged disposable tenant was requested through `POST /leads/v2/tenants` using only non-secret request data: a generated `task6-disposable-*` name, starter plan, portal delivery, and an empty delivery configuration. The configured service boundary returned **HTTP 401** before returning a tenant identifier.

Because no authenticated non-secret scope-creation path was available, the test stopped before any durable scope or job was created. No service credential was read, printed, requested, synthesized, or passed from `.env`.

- Disposable tenant ID: **not issued**
- Disposable ICP ID: **not created**
- Approved real tenant used: **no**
- Durable job/reservation/assignment/delivery created: **no evidence of creation; submission was not attempted**

## Required matrix

| Matrix case | Disposable durable result | Reason |
|---|---|---|
| First submission | **NOT_TESTED** | No disposable tenant/scope could be admitted safely |
| Equivalent same-key replay | **NOT_TESTED** | First submission was not permitted |
| Changed-payload replay | **NOT_TESTED** | First submission was not permitted |
| Fresh-key submission after terminal/fenced work | **NOT_TESTED** | No prior disposable job existed |

The durable assertions for stable identity, duplicate job/reservation/assignment/delivery suppression, non-disclosing conflict, fresh identity after fencing, provider-call count, and deployed uniqueness therefore remain **BLOCKED**, not passed.

## Local contract separation

Existing local/in-memory replay contracts remain separate from this rerun and do not promote to disposable durable evidence. The prior local report documented those contracts as passing, but this rerun did not relabel them as durable API/worker/database evidence.

## Stop decision

**BLOCKED.** The requested disposable scope could not be created through the configured authenticated service boundary without using a credential. No fallback to the real tenant or direct database/bypass path was used. The next attempt requires an operator-approved, non-secret test-scope admission mechanism or an already authenticated disposable-scope session; do not delete anything from this run because no durable test records were created.
