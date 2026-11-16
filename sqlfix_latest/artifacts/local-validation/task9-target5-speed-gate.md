# Task 9: Target5 Final Local Synthetic Gate

Date: 2026-11-13
Evidence class: `synthetic/offline`
Environment: local Python test process with provider/network/secret access denied by test configuration

This report records only local deterministic fixtures. It does not claim production behavior, live-provider behavior, or a 20–30 second result.

## Commands and results

All commands ran from `sqlfix_latest`. Each test command set `LOCAL_VALIDATION_MODE=true` and blank `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `SUPABASE_URL`, and `SUPABASE_SERVICE_KEY`. No `.env` file was read.

| Check | Exact command | Result |
|---|---|---|
| Task 1 exact exploration suite | `python -m pytest -q tests/test_target5_speed_optimization_exploration.py -rA` | **PASS**, 16 passed, exit 0 |
| Task 2 exact preservation suite | `python -m pytest -q tests/test_target5_speed_optimization_preservation.py -rA` | **PASS**, 514 passed, exit 0 |
| Focused route/enrichment/batch/target-stop/CRM/delivery/realtime suite | `python -m pytest -q --tb=short tests/test_target5_route_coordinator.py tests/test_target5_enrichment_waterfall.py tests/test_target5_safe_batch_boundaries.py tests/test_lead_generation_target_stop.py tests/test_task5_target_reconciliation.py tests/test_orchestration_crm_delivery.py tests/test_crm_delivery_adapters.py tests/test_crm_projection_contract.py tests/test_task6_assignment_delivery_reconciliation.py tests/test_task8_ordered_delivery.py tests/test_task6_realtime.py tests/test_task21_realtime_migration.py tests/test_task24_realtime_validation.py tests/test_target5_speed_instrumentation.py -rA` | **PASS**, 85 passed, exit 0 |
| Benchmark and timing contract tests | `python -m pytest -q --tb=short tests/test_target5_speed_benchmarks.py tests/test_target5_speed_instrumentation.py -rA` | **PASS**, 5 passed, exit 0 |
| Synthetic benchmark matrix | `python scripts/target5_speed_benchmark.py` | **PASS**, measured targets 5/10/50; target 500 not executed; exit 0 |

The first attempt to invoke `pytest` directly was unavailable on PATH. All requested suites were then run successfully with `python -m pytest`; this is the command recorded above.

## Gate classification

| Gate / invariant | Status | Evidence |
|---|---|---|
| Exact task-1 exploration suite | **PASS** | 16/16 passed against the implemented bounded behavior and offline fixtures. |
| Exact task-2 preservation suite | **PASS** | 514/514 passed, including supported targets 1–500, tenant scope, uniqueness, CRM, delivery, cancellation, and provider denial. |
| No post-target admission | **PASS** | Route coordinator target-stop tests passed for target 5/10/50; target stop admitted no later route. |
| No provider overrun | **PASS** | Focused route test reported `provider_overrun_count=0`; benchmark reported `provider_calls=0` and paid fallback count 0. |
| No queue leak | **PASS** | Cancellation/route fixtures reported fenced work and drained bounded queues; benchmark queue status was `drained`. |
| No late authoritative write | **PASS** | Cancellation fencing and timing tests passed with `late_writes=0`. |
| No duplicate effect | **PASS** | Persistence, assignment, CRM projection, outbox, and delivery retry tests passed with idempotent results. |
| Valid partial enrichment preserved | **PASS** | Slow-free-stage and deadline fixtures retained valid partial fields and prevented later paid work. |
| Authoritative count reconciliation | **PASS** | Batch/delivery/reconciliation tests used persisted counts and passed duplicate/partial-count cases. |
| Hard cap | **PASS** | Target 500 accepted at the request boundary, target 501 rejected, and no 500-lead workload executed. |
| Complete sanitized timing artifacts | **PASS** | Benchmark schema validation and instrumentation tests passed for targets 5/10/50. |
| Provider/network/secret access denied | **PASS** for local test contract | Local validation fixtures made zero provider calls and forbidden-action counters were all zero. This is not a host-level firewall audit. |
| Real provider/network integration | **NOT_TESTED** | Explicitly excluded by the task. |
| Bounded real target measurement | **BLOCKED** | Task 10 requires separate authorization and approved live prerequisites. No live run was attempted. |
| Production latency or 20–30 second success | **NOT_TESTED** | Synthetic timings cannot establish deployed/provider/production speed. |

## Synthetic benchmark table

Source: `artifacts/local-validation/target5-speed-benchmark.json`.

| Target | Result | Total elapsed seconds | Discovery ms | Enrichment ms | Persistence ms | Assignment ms | Delivery ms | Configured route limit | Observed route max | Queue max | Provider calls | Paid fallback | Assigned/reserved/delivered | Stop |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 5 | MEASURED | 0.000840 | 0.798 | 0.001 | 0.000 | 0.000 | 0.000 | 3 | 1 | 3 | 0 | 0 | 5/5/5 | target_met |
| 10 | MEASURED | 0.000489 | 0.465 | 0.001 | 0.001 | 0.001 | 0.001 | 3 | 1 | 3 | 0 | 0 | 10/10/10 | target_met |
| 50 | MEASURED | 0.000493 | 0.463 | 0.001 | 0.001 | 0.000 | 0.000 | 3 | 1 | 3 | 0 | 0 | 50/50/50 | target_met |

Common matrix evidence:

- `evidence_class`: `synthetic/offline`
- `provider_mode`: `local_synthetic_denied`
- `queue_outcome`: `drained`, `discarded_after_stop=0`
- `cancellation_outcome`: `late_writes=0`, `fenced=0` for the non-cancelled benchmark runs
- `provider_overrun_count`: 0
- forbidden action counters: email 0, outreach 0, campaign 0, Stripe 0, SES 0
- target 500: request-boundary validation only; `executed=false`; target 501 rejected

## Remaining limitations

- These are synthetic in-memory timings and do not predict provider latency, network latency, deployed worker/queue behavior, or production speed.
- The benchmark's observed route concurrency was 1 despite a configured limit of 3 because the local fixture completed too quickly; this is not evidence of production concurrency capacity.
- No real provider, Supabase/Postgres, Redis, network, credentials, email, outreach, campaign, Stripe, or SES path was exercised.
- No destructive SQL, migration, reset, or remote operation was run.
- Task 10 remains blocked until separately authorized live prerequisites are approved. No production or 20–30 second claim is made.
