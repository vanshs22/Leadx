-- workflow_runs.workflow_id referenced the legacy workflow_rules table, but runs are created for workflow_definitions rows
-- (the CRM Automations feature). Every custom automation therefore failed with a foreign-key error and never executed.
-- Re-point the key at workflow_definitions. NOT VALID keeps any pre-existing legacy run rows untouched; new rows are checked.
-- Rollback: drop workflow_runs_workflow_definition_fkey and re-add the old constraint referencing workflow_rules(id) NOT VALID.
DO $$
BEGIN
  IF to_regclass('public.workflow_runs') IS NOT NULL AND to_regclass('public.workflow_definitions') IS NOT NULL THEN
    ALTER TABLE public.workflow_runs DROP CONSTRAINT IF EXISTS workflow_runs_workflow_id_fkey;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'workflow_runs_workflow_definition_fkey') THEN
      ALTER TABLE public.workflow_runs
        ADD CONSTRAINT workflow_runs_workflow_definition_fkey
        FOREIGN KEY (workflow_id) REFERENCES public.workflow_definitions(id) ON DELETE CASCADE NOT VALID;
    END IF;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
