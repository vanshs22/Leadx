-- LeadX 15.8: durable pipeline-run lifecycle compatibility.
-- The API creates a run before enqueueing the worker, so `queued` must be a
-- valid pipeline_runs.status on both fresh and previously provisioned databases.
DO $$
DECLARE
  c record;
BEGIN
  IF to_regclass('public.pipeline_runs') IS NOT NULL THEN
    FOR c IN
      SELECT conname
      FROM pg_constraint
      WHERE conrelid = 'public.pipeline_runs'::regclass
        AND contype = 'c'
        AND pg_get_constraintdef(oid) ILIKE '%status%'
        AND pg_get_constraintdef(oid) ILIKE '%running%'
        AND pg_get_constraintdef(oid) ILIKE '%completed%'
    LOOP
      EXECUTE format('ALTER TABLE public.pipeline_runs DROP CONSTRAINT IF EXISTS %I', c.conname);
    END LOOP;

    -- Existing installations may contain historical labels from before the
    -- durable lifecycle contract. Normalize them before adding the stricter
    -- constraint; preserve successful work and mark ambiguous terminal work
    -- as partial when it produced any output.
    UPDATE public.pipeline_runs
    SET status = CASE lower(trim(COALESCE(status, '')))
      WHEN 'queued' THEN 'queued'
      WHEN 'pending' THEN 'queued'
      WHEN 'created' THEN 'queued'
      WHEN 'waiting' THEN 'queued'
      WHEN 'running' THEN 'running'
      WHEN 'processing' THEN 'running'
      WHEN 'started' THEN 'running'
      WHEN 'in_progress' THEN 'running'
      WHEN 'in-progress' THEN 'running'
      WHEN 'completed' THEN 'completed'
      WHEN 'complete' THEN 'completed'
      WHEN 'done' THEN 'completed'
      WHEN 'success' THEN 'completed'
      WHEN 'succeeded' THEN 'completed'
      WHEN 'failed' THEN 'failed'
      WHEN 'error' THEN 'failed'
      WHEN 'errored' THEN 'failed'
      WHEN 'partial' THEN 'partial'
      WHEN 'partially_completed' THEN 'partial'
      WHEN 'partial_success' THEN 'partial'
      WHEN 'cancelled' THEN CASE
        WHEN COALESCE(assigned, 0) > 0 OR COALESCE(delivered, 0) > 0 THEN 'partial'
        ELSE 'failed'
      END
      WHEN 'canceled' THEN CASE
        WHEN COALESCE(assigned, 0) > 0 OR COALESCE(delivered, 0) > 0 THEN 'partial'
        ELSE 'failed'
      END
      ELSE CASE
        WHEN COALESCE(assigned, 0) > 0 OR COALESCE(delivered, 0) > 0 THEN 'partial'
        ELSE 'failed'
      END
    END
    WHERE lower(trim(COALESCE(status, ''))) NOT IN
      ('queued','running','completed','failed','partial');

    ALTER TABLE public.pipeline_runs
      ADD CONSTRAINT pipeline_runs_status_check
      CHECK (status IN ('queued','running','completed','failed','partial'));
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
