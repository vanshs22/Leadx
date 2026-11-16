# Task 6 Replay Matrix Report

- **Task:** `live-target5-validation.6`
- **Overall status:** `BLOCKED`
- **Local contract status:** `PASS` for the bounded in-memory/local contracts listed below
- **Durable API/worker status:** `BLOCKED` / `NOT_TESTED`
- **Evidence class:** `local` for contract tests; no durable-backend or live evidence
- **Scope:** synthetic/in-memory fixtures only; the approved real tenant was not used

## Gate decision

Task 4 runtime readiness was not proven. The existing sanitized Task 4 report records Docker, API, worker, Redis, and queue readiness as unavailable or blocked. The replay matrix therefore did not use the local API/worker/durable boundary and did not create any real or durable submission.

No Docker start/recovery, remote-service contact, SQL, migration, reset, cleanup, queue operation, provider call, email, outreach, campaign, Stripe, or SES action was attempted.

## Bounded matrix evidence

The requested matrix was represented by existing local contracts, without modifying application code:

| Matrix case | Local contract result | Evidence |
|---|---|---|
| First submission | `PASS` | The in-memory intake and durable-conflict contracts admit one initial identity and retain one stored row/effect. |
| Equivalent same-key replay | `PASS` | The replay returns the original request/job result; the in-memory store records one create and one stable identity. |
| Changed-payload replay | `PASS` | The request fails with a bounded conflict and does not disclose the original ID/hash or create a second row. |
| Fresh key after terminal/fenced prior work | `PASS` | Fresh identity executes once after terminal replay; terminal/reset-work and cancelled-owner checks reject unsafe reuse or late completion. |

## Local contract checks run

All explicit test node IDs below passed. These are in-memory or local contract tests, not a running API/worker/database test.

- `tests/test_task4_durable_conflicts.py`: matching conflict replay, non-disclosing foreign conflict, active duplicate/fresh key identity separation, verified active duplicate handling, unresolved/dependency conflict handling, HTTP conflict fallback, and global duplicate handling.
- `tests/test_orchestration_intake.py`: same-key replay, changed-input conflict, fresh identity across validity windows.
- `tests/runtime_preservation/test_healthy_runtime_property.py`: stable idempotency replay.
- `tests/test_orchestration_crm_delivery.py`: concurrent reservation/assignment replay, idempotent CRM projection, idempotent outbox creation/retry, and duplicate-worker delivery suppression.
- `tests/test_task3_6_fresh_run_admission.py`: terminal/cross-tenant/reset-work rejection and reconciled terminal-scope verification.
- `tests/test_task9_orchestration_lifecycle.py`: terminal replay/fresh run identity and late-write fencing.
- `tests/test_scoped_work_reconciliation.py`: cancelled-job owner fencing.

The explicit runs produced **23 passed tests**. An earlier combined command was interrupted after an overly broad command-line filter; its output was not used as evidence. The tests were rerun with explicit node IDs and bounded timeouts.

## Assertion boundary

| Required assertion | Local contract evidence | Durable-backend evidence |
|---|---|---|
| One stable replay result | `PASS` in memory | `BLOCKED` / `NOT_TESTED` |
| No second durable job | `PASS` for fake/local stores | `BLOCKED` / `NOT_TESTED` |
| No second reservation or assignment | `PASS` | `BLOCKED` / `NOT_TESTED` |
| No second delivery effect | `PASS`; duplicate worker callback ran once | `BLOCKED` / `NOT_TESTED` for a real provider |
| No provider/outbound call | `PASS` for local isolation; no provider was configured or contacted | `BLOCKED` / `NOT_TESTED` |
| Changed payload is safe and non-disclosing | `PASS` | `BLOCKED` / `NOT_TESTED` |
| Fresh identity only after terminal/fencing | `PASS` for local admission/lifecycle contracts | `BLOCKED` / `NOT_TESTED` |
| Existing database uniqueness constraint/index remains active | `NOT_TESTED` at database level | `BLOCKED` because durable readiness was not proven |
| Tenant isolation | `PASS` for local conflict/admission contracts | `BLOCKED` / `NOT_TESTED` |

The local checks demonstrate contract behavior only. They do not prove that the deployed or running local database constraint/index is present, nor that a real worker would avoid duplicate durable effects.

## Safety record

- Approved real tenant used: `no`
- Real target-five submission created: `no`
- Durable job/reservation/assignment/delivery created: `no`
- Remote service contacted: `no`
- Provider/outbound calls: `0`
- Email/outreach/campaign/Stripe/SES: `0`
- `.env` or credentials read/printed/requested: `no`
- SQL/migrations/reset/cleanup/queue clearing: `no`

## Required next gate

Do not mark Task 6 complete from this report. Re-run the same four-case matrix only after Task 4 readiness is independently `PASS` and Task 3 authorizes a disposable/test scope. The durable run must remain outside the approved real tenant unless separately authorized, and the matrix must record only opaque IDs, reason/status codes, counts, and fingerprints.
