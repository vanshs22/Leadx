# LeadX 15.8 Pipeline Fix Note — V14

The pipeline 404 and missing Run History were traced end-to-end rather than patched at the polling endpoint.

### Failure chain

1. `POST /leads/v2/pipeline/run` generated a `run_id`.
2. The API attempted to insert `pipeline_runs.status = 'queued'`.
3. The legacy `pipeline_runs` CHECK constraint did not permit `queued`.
4. The INSERT exception was swallowed.
5. The API still returned `200` with `status=queued` and the `run_id`.
6. The worker could run, but the UI's `GET /leads/v2/admin/runs/{run_id}` had no row to return.
7. Run History also had no row because it reads `pipeline_runs`.

### Fix

V14 makes run persistence durable and verified, adds the `queued` lifecycle state through migration 23, fails closed when persistence fails, and improves frontend/error diagnostics.
