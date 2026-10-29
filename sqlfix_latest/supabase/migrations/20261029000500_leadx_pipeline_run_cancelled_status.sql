-- Allow durable operator/client cancellation as a terminal pipeline state.
DO $$
DECLARE constraint_name text;
BEGIN
  IF to_regclass('public.pipeline_runs') IS NOT NULL THEN
    FOR constraint_name IN
      SELECT conname
      FROM pg_constraint
      WHERE conrelid = 'public.pipeline_runs'::regclass
        AND contype = 'c'
        AND pg_get_constraintdef(oid) ILIKE '%status%'
        AND pg_get_constraintdef(oid) ILIKE '%completed%'
    LOOP
      EXECUTE format('ALTER TABLE public.pipeline_runs DROP CONSTRAINT IF EXISTS %I', constraint_name);
    END LOOP;
    ALTER TABLE public.pipeline_runs
      ADD CONSTRAINT pipeline_runs_status_check
      CHECK (status IN ('queued','running','completed','failed','partial','cancelled'));
  END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
NOTIFY pgrst, 'reload schema';
