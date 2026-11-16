# Task 5: Sanitized deployed read-only evidence for the historical `23505`

- **Spec:** `live-target5-validation`
- **Task:** 5, obtain sanitized deployed read-only evidence for the `23505` source
- **Task evidence result:** `PASS` for the approved read-only evidence acquisition
- **Historical `23505` classification:** `UNRESOLVED`
- **Evidence class:** `remote-read-only`
- **Runtime prerequisite:** local runtime gate was previously recorded `PASS`; the worker used below was running and healthy at the time of the reads
- **Mutation result:** no migration, SQL DDL, INSERT/UPDATE/DELETE, mutating RPC, retry, cancel, reset, cleanup, queue operation, provider call, pipeline run, email, outreach, campaign, Stripe, or SES action was attempted
- **Secret handling:** `.env` contents, environment values, credentials, tokens, raw request bodies, raw idempotency keys, tenant UUIDs, response payloads, job payloads, contact values, and customer data were not read, printed, or persisted

## Approved read-only boundary used

The healthy `worker` container's already configured application client was used. The following operations were performed only through that client:

1. `rpc('leadx_verify_durable_preflight', {})`.
2. A bounded `job_operations` projection limited to 20 rows, selecting only opaque operation/job IDs, operation kind, existing one-way scope/request fingerprints, status, accounting status, and timestamps. The raw `idempotency_key`, tenant identifier, and `response` were not selected.
3. A bounded `jobs` projection for the returned opaque job IDs, limited to 20 rows, selecting status, attempt limits, run/start/finish timestamps, and lease-presence/timestamp fields. Payload, result, error text, and lock owner were not selected.
4. The existing non-mutating `leadx_queue_stats()` read-only status RPC.

The raising `leadx_require_durable_preflight` wrapper was **not** called.

## Sanitized preflight result

Observed from `public.leadx_verify_durable_preflight()`:

```json
{
  "status": "PASS",
  "ok": true,
  "missing": [],
  "migration_chain": "20261001000800..20261001000950",
  "checked_at": "2026-11-12T10:27:17.700648+00:00",
  "safe_to_start_provider_work": true
}
```

This proves that the deployed non-mutating preflight reported no missing item at the time observed. It does not authorize a target-five run by itself.

## Function, index, constraint, RLS, and grant identity

| Boundary | Sanitized observed result |
|---|---|
| `public.leadx_verify_durable_preflight()` | `PASS`; callable through the worker's configured client and returned a result object |
| `public.leadx_require_durable_preflight()` | `NOT_CALLED` by instruction; no raising wrapper result was needed |
| `public.leadx_reap_stale_mission_tasks(integer)` | Preflight missing-list check passed; function identity was not returned as a catalog row |
| `public.leadx_probe_queue_capability(text,timestamptz)` | Preflight missing-list check passed; function identity was not returned as a catalog row |
| `public.leadx_prepare_job(uuid,text,text,text,text,text,jsonb,integer,boolean,integer,integer,integer,text)` | Preflight missing-list check passed |
| `public.leadx_finish_job_operation(uuid,text,jsonb)` | Preflight missing-list check passed |
| `public.leadx_reopen_job_operation(uuid)` | Preflight missing-list check passed |
| `public.uq_job_operations_operation_key` or compatibility `public.job_operations_operation_kind_idempotency_key_key` | Preflight alternative-name check passed; the selected alternative was not separately exposed |
| `public.uq_job_operations_job` or compatibility `public.job_operations_job_id_key` | Preflight alternative-name check passed; the selected alternative was not separately exposed |
| `public.idx_job_operations_tenant_created` | Preflight missing-list check passed |
| Required operation uniqueness | The preflight index family check passed. The underlying remote constraint name was not separately returned, so the exact catalog constraint identity is `NOT_OBSERVED`. |
| `job_operations` RLS | `PASS` as checked by the preflight. No `rls:job_operations` item was returned. |
| `service_role` `SELECT` on `job_operations` | `PASS` as checked by the preflight. No `grant:service_role.job_operations` item was returned. |
| `service_role` `EXECUTE` on `leadx_prepare_job` | `PASS` as checked by the preflight. No `grant:service_role.leadx_prepare_job` item was returned. |
| Other grants/policies | `NOT_OBSERVED`; they were not broadened into an unbounded catalog query. |

The empty `missing` list is evidence for the checks implemented by the approved function. It is not a claim that every remote function, index, constraint, policy, or grant was independently catalog-enumerated.

## Bounded queue/status observations

The `job_operations` projection returned 20 rows, all linked to opaque job IDs. The `pipeline.run` subset contained 15 rows with status counts:

| Projection | Result |
|---|---|
| `job_operations` rows read | `20` of maximum `20` |
| `pipeline.run` rows in that sample | `15` |
| `pipeline.run` status counts | `completed=5`, `rejected=9`, `cancelled=1` |
| Job status projection rows | `20` of maximum `20` |
| Queue status RPC | `failed=7`, `cancelled=116`, `done=8141`, `running=2` |
| Raw payload/result/error fields selected | `0` |
| Mutating queue calls | `0` |

The linked job projection showed terminal timestamps and no lease expiry value for the sampled rows. This is bounded status evidence only and is not evidence that the historical `23505` event belongs to any sampled row.

## Available one-way operation fingerprints

The following are the 15 bounded `pipeline.run` rows observed in the same 20-row sample. `scope_fingerprint` and `request_fingerprint` are existing one-way values; opaque IDs are retained only for correlation. No raw key, tenant identifier, payload, or response is included.

| Operation ID | Job ID | Scope fingerprint | Request fingerprint | Status | Created | Updated |
|---|---|---|---|---|---|---|
| `502041cb-c872-4bef-b146-c025c7c7f7ad` | `4506afe6-f1f4-41a9-8488-e5549b014354` | `b73e057f01a70ebeaa2a7fe214f03e6563d41e29d5d40503e82ec24e2b73671d` | `7333b1833b5fe5ab7781b6ab998183f596a981cd8c6c4dcb701dce1e2e1e8db4` | rejected | `2026-11-11T18:10:02.703472+00:00` | `2026-11-11T19:02:38.949783+00:00` |
| `d32759b9-4cde-4d7d-b57e-deef50fb0d91` | `5b264275-e6d4-4cd4-b918-73d5313c4aea` | same as above | `7333b1833b5fe5ab7781b6ab998183f596a981cd8c6c4dcb701dce1e2e1e8db4` | rejected | `2026-11-11T17:50:15.746099+00:00` | `2026-11-11T18:08:56.244795+00:00` |
| `69e1bb6e-065f-4e52-a4c7-97a47030e374` | `b97a90d1-717d-49fe-b840-faa2c2bf41dd` | same as above | `f1d410dd5e1239b88a49b33739701ff4b90b7d79181d0ec860b8657eb8ec01f6` | rejected | `2026-11-11T14:20:12.932134+00:00` | `2026-11-11T17:43:15.326659+00:00` |
| `448d1917-851c-476b-aca7-d523f3c20f7d` | `ab19a6f0-6b65-4d62-b4b1-dc0a1a6939bc` | same as above | `2db1c3f096fc2a02617f6eccd3ef339bc21ef8b5058e21b5ea45ddd0d70826bc` | rejected | `2026-11-11T14:12:47.020494+00:00` | `2026-11-11T14:12:52.174668+00:00` |
| `851d472b-acf3-4733-8855-2aa690590c56` | `37a93629-85f8-427c-875d-ae64136922be` | same as above | `2db1c3f096fc2a02617f6eccd3ef339bc21ef8b5058e21b5ea45ddd0d70826bc` | rejected | `2026-11-11T14:12:38.984069+00:00` | `2026-11-11T14:12:44.075205+00:00` |
| `b357ab75-8b67-4389-b89f-b7d88d7c705f` | `76f3c5d8-54dc-4bb8-bf37-29a94d531c4f` | same as above | `84e4d3d058d8e7b16fddb0edc6069f4fbdb6ab45afbc3f72d328916bb43bfeab` | rejected | `2026-11-11T13:50:02.853856+00:00` | `2026-11-11T13:51:01.746983+00:00` |
| `6cd153a5-9b91-4261-9452-77946f70cf0e` | `9df4999c-832f-4c02-be85-bdd6a0111fde` | same as above | `ae1f86071a604cadcf8e62fa345a336b16d97a1e9e7dc765822f99d8d83b9f21` | cancelled | `2026-10-29T17:02:17.353477+00:00` | `2026-10-29T17:09:55.157427+00:00` |
| `fcd1914a-4ce0-4a09-9f7e-adfc5bfa08d4` | `baae616d-50d0-49cf-b6f1-bda12872bd60` | same as above | `1a9c22494a863e834ebab2d2eaf69ef1997db7ba691c24cc0b8951556ccd536b` | completed | `2026-10-28T21:51:53.315233+00:00` | `2026-10-28T21:54:57.99866+00:00` |
| `a6177a5a-3b31-4a4c-adfa-b0751a2af57c` | `f1844fd5-31b6-4754-8f69-860d6bd3f100` | same as above | `5e194be5800710e531d7f48f119ee006e49daeafe0dc8deb9ac539d09919a1d9` | completed | `2026-10-28T21:43:01.776409+00:00` | `2026-10-28T21:48:21.049116+00:00` |
| `b9db72cf-26a9-4427-a878-0e502b21e76d` | `ab8dd09c-ac65-49de-9457-2e20b7673ac0` | same as above | `1d71b1484b0313429cfe45ea82ef686720af71ee9c12ef820a349d1a78cc7326` | completed | `2026-10-28T21:29:52.278307+00:00` | `2026-10-28T21:42:50.716789+00:00` |
| `28922ee5-d012-4a48-9d0e-d8c25b8eac69` | `24b5ee39-f725-4423-9673-a092c69cb25f` | same as above | `aea0a0292d56f098241676c38fc94bd264c5672c75360eab803c63658e08311d` | rejected | `2026-10-28T20:15:10.359034+00:00` | `2026-10-28T21:04:39.553975+00:00` |
| `4636c448-d287-4aac-8a8a-7585865254bd` | `b0f1d43c-ce63-46eb-872b-3bfacd99cd5e` | same as above | `5cc5a2157dd3614a0ceae612fee68f638d9823806b811020adb903fa4c67dc6a` | completed | `2026-10-28T19:48:03.469312+00:00` | `2026-10-28T19:56:02.550886+00:00` |
| `6c8f57da-69f5-412e-b8ec-8898e2d1212d` | `5e6bd4ab-5d35-4c38-beda-3db2539a6b2d` | same as above | `5cc5a2157dd3614a0ceae612fee68f638d9823806b811020adb903fa4c67dc6a` | completed | `2026-10-28T19:35:00.096281+00:00` | `2026-10-28T19:38:39.913632+00:00` |
| `4bd3909b-6fd1-42ff-a2b4-f75fd3452ccb` | `d2b23a15-55a9-43b0-b9bb-dc6a6179837a` | same as above | `5cc5a2157dd3614a0ceae612fee68f638d9823806b811020adb903fa4c67dc6a` | rejected | `2026-10-27T19:26:48.96272+00:00` | `2026-10-27T20:24:48.949798+00:00` |
| `b8d02acd-c68f-45c7-9a92-0fcff5e23cee` | `8b183906-6ffe-40ec-8c86-ae99bddaf026` | same as above | `36be1d7cc9403c20866c35d0ae6686656eb0bc12ab807705ba6fade0065850ad` | rejected | `2026-10-25T21:17:17.807259+00:00` | `2026-10-27T18:14:16.079547+00:00` |

The operation sample contains repeated request fingerprints with different durable operation/job IDs. That observation alone does not prove the historical `23505` was a matching replay, conflicting payload, active duplicate, stale lease overlap, or dependency failure because the raw idempotency key, event correlation ID, error event, and request scope for the historical error were not available.

## Classification of the historical `23505`

| Candidate class | Result | Evidence basis |
|---|---|---|
| Matching replay | `NOT_PROVEN` | Existing rows contain repeated fingerprints, but no historical error event or idempotency-key correlation ties one row to the reported `23505`. |
| Conflicting payload/scope | `NOT_PROVEN` | No raw identity was selected and no bounded event record exposed a mismatch reason. |
| Active duplicate | `NOT_PROVEN` | Sampled jobs were terminal or had no lease expiry, but the historical error was not tied to a sampled job. |
| Stale lease overlap | `NOT_PROVEN` | The bounded sample did not show an active lease, but it cannot establish the historical event's lease state. |
| Dependency/database failure | `NOT_PROVEN` | The preflight now passes; that does not explain the earlier `23505` event. |
| **Overall** | **`UNRESOLVED`** | Evidence proves the approved preflight boundary is healthy now, but does not prove the source classification of the historical `23505`. |

## Exact evidence decision

- **Preflight:** `PASS`, remote-read-only.
- **Required durable object checks:** `PASS` according to the function's empty `missing` list.
- **RLS/grant checks implemented by preflight:** `PASS`.
- **Bounded status/catalog reads:** `PASS`; no raw payload fields selected.
- **Historical `23505` source classification:** `UNRESOLVED`, not promoted from correlation or repeated fingerprints.
- **Provider/live readiness:** `NOT_TESTED` by this task. No provider or pipeline work was started.

The approved function-only patch is therefore observed to be effective for the non-mutating preflight call. The remaining `23505` diagnosis stays unresolved because the available remote evidence does not identify the historical event's operation identity or constraint name.
