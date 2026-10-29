-- LeadX additive scoped-work transition primitive.
-- This function only locks rows selected by one explicit run or tenant scope.
-- It does not delete data, create an operation ledger, or infer a global scope.

CREATE OR REPLACE FUNCTION public.leadx_transition_scoped_work(
  p_scope_kind text,
  p_run_id uuid DEFAULT NULL,
  p_tenant_id uuid DEFAULT NULL,
  p_cutoff timestamptz DEFAULT NULL,
  p_status_allowlist text[] DEFAULT ARRAY['queued','running','retrying']::text[],
  p_apply boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_scope text := lower(btrim(coalesce(p_scope_kind, '')));
  v_active text[] := ARRAY['queued','running','retrying']::text[];
  v_run_count integer := 0;
  v_job_count integer := 0;
  v_agent_run_count integer := 0;
  v_mission_count integer := 0;
  v_task_count integer := 0;
  v_reservation_count integer := 0;
  v_transitioned integer := 0;
  v_changed integer := 0;
  v_accounting_pending integer := 0;
  v_remaining integer := 0;
  v_remaining_active integer := 0;
  v_remaining_stale integer := 0;
  v_release integer;
  v_run_changed integer := 0;
  v_job_changed integer := 0;
  v_agent_run_changed integer := 0;
  v_task_changed integer := 0;
  v_mission_changed integer := 0;
  v_report_limit integer := 500;
  v_selected_by_status jsonb := '{}'::jsonb;
  v_transitioned_by_table jsonb := '{}'::jsonb;
  v_skipped_by_table jsonb := '{}'::jsonb;
  v_failed_reason_codes jsonb := '[]'::jsonb;
  v_skipped_reason_codes jsonb := '[]'::jsonb;
  v_result jsonb;
  v_job_id uuid;
  v_reservation_id uuid;
BEGIN
  IF v_scope NOT IN ('run','tenant_active','tenant_stale') THEN
    RETURN jsonb_build_object('ok',false,'reason','unsupported_scope');
  END IF;
  IF p_status_allowlist IS NULL OR cardinality(p_status_allowlist) = 0
     OR NOT (p_status_allowlist <@ v_active) THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid_status_allowlist');
  END IF;
  IF v_scope = 'run' THEN
    IF p_run_id IS NULL OR p_tenant_id IS NOT NULL OR p_cutoff IS NOT NULL THEN
      RETURN jsonb_build_object('ok',false,'reason','invalid_run_scope');
    END IF;
  ELSIF p_tenant_id IS NULL OR p_run_id IS NOT NULL THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid_tenant_scope');
  ELSIF v_scope = 'tenant_stale' AND p_cutoff IS NULL THEN
    RETURN jsonb_build_object('ok',false,'reason','cutoff_required');
  ELSIF v_scope = 'tenant_active' AND p_cutoff IS NOT NULL THEN
    RETURN jsonb_build_object('ok',false,'reason','cutoff_not_allowed');
  END IF;

  CREATE TEMP TABLE IF NOT EXISTS _leadx_reset_runs(id uuid PRIMARY KEY) ON COMMIT DROP;
  CREATE TEMP TABLE IF NOT EXISTS _leadx_reset_jobs(id uuid PRIMARY KEY) ON COMMIT DROP;
  CREATE TEMP TABLE IF NOT EXISTS _leadx_reset_agent_runs(id uuid PRIMARY KEY) ON COMMIT DROP;
  CREATE TEMP TABLE IF NOT EXISTS _leadx_reset_missions(id uuid PRIMARY KEY) ON COMMIT DROP;
  CREATE TEMP TABLE IF NOT EXISTS _leadx_reset_tasks(id uuid PRIMARY KEY) ON COMMIT DROP;
  CREATE TEMP TABLE IF NOT EXISTS _leadx_reset_reservations(id uuid PRIMARY KEY) ON COMMIT DROP;
  TRUNCATE _leadx_reset_runs, _leadx_reset_jobs, _leadx_reset_agent_runs,
    _leadx_reset_missions, _leadx_reset_tasks, _leadx_reset_reservations;

  -- Lock parent runs first. The scope predicate is repeated by every child
  -- selection, so a tenant or stale reset cannot widen through a join.
  INSERT INTO _leadx_reset_runs(id)
  SELECT pr.id
  FROM public.pipeline_runs pr
  WHERE pr.status = ANY(p_status_allowlist)
    AND ((v_scope = 'run' AND pr.id = p_run_id)
      OR (v_scope <> 'run' AND pr.tenant_id = p_tenant_id
          AND (v_scope = 'tenant_active'
            OR COALESCE(pr.updated_at, pr.started_at, pr.created_at) <= p_cutoff)))
  ORDER BY pr.id
  FOR UPDATE;
  SELECT count(*) INTO v_run_count FROM _leadx_reset_runs;

  INSERT INTO _leadx_reset_jobs(id)
  SELECT j.id
  FROM public.jobs j
  WHERE j.status = ANY(p_status_allowlist)
    AND ((v_scope = 'run' AND EXISTS (
            SELECT 1 FROM _leadx_reset_runs r
            WHERE j.id = r.id OR j.payload->>'run_id' = r.id::text))
      OR (v_scope <> 'run' AND j.tenant_id = p_tenant_id
          AND (v_scope = 'tenant_active'
            OR COALESCE(j.locked_at, j.created_at) <= p_cutoff)))
  ORDER BY j.id
  FOR UPDATE;
  SELECT count(*) INTO v_job_count FROM _leadx_reset_jobs;

  -- Agent runs are selected by the pipeline linkage carried in input_data or
  -- by a task already selected in this same transaction.
  INSERT INTO _leadx_reset_agent_runs(id)
  SELECT ar.id
  FROM public.agent_runs ar
  WHERE ar.status IN ('queued','running')
    AND ((v_scope = 'run' AND EXISTS (
            SELECT 1 FROM _leadx_reset_runs r
            WHERE ar.input_data->>'run_id' = r.id::text))
      OR (v_scope <> 'run' AND ar.tenant_id = p_tenant_id
          AND (v_scope = 'tenant_active'
            OR COALESCE(ar.updated_at, ar.created_at) <= p_cutoff)))
  ORDER BY ar.id
  FOR UPDATE;

  INSERT INTO _leadx_reset_tasks(id)
  SELECT t.id
  FROM public.agent_mission_tasks t
  WHERE t.tenant_id = CASE WHEN v_scope = 'run' THEN t.tenant_id ELSE p_tenant_id END
    AND (t.status = ANY(p_status_allowlist) OR t.status = 'blocked')
    AND ((v_scope = 'run' AND (
            t.input_data->>'run_id' = p_run_id::text
            OR t.agent_run_id IN (SELECT id FROM _leadx_reset_agent_runs)))
      OR (v_scope <> 'run' AND (
            t.tenant_id = p_tenant_id
            AND (v_scope = 'tenant_active'
              OR COALESCE(t.lease_expires_at, t.updated_at, t.created_at) <= p_cutoff))))
  ORDER BY t.id
  FOR UPDATE;

  INSERT INTO _leadx_reset_missions(id)
  SELECT DISTINCT t.mission_id
  FROM public.agent_mission_tasks t
  WHERE t.id IN (SELECT id FROM _leadx_reset_tasks)
  ON CONFLICT DO NOTHING;
  INSERT INTO _leadx_reset_missions(id)
  SELECT m.id
  FROM public.agent_missions m
  WHERE m.status IN ('queued','running')
    AND ((v_scope = 'run' AND m.input_data->>'run_id' = p_run_id::text)
      OR (v_scope <> 'run' AND m.tenant_id = p_tenant_id
          AND (v_scope = 'tenant_active'
            OR COALESCE(m.updated_at, m.created_at) <= p_cutoff)))
  ON CONFLICT DO NOTHING;

  -- A selected mission owns all of its non-terminal child tasks, including
  -- dependency-blocked children that otherwise could strand the parent.
  INSERT INTO _leadx_reset_tasks(id)
  SELECT t.id
  FROM public.agent_mission_tasks t
  WHERE t.mission_id IN (SELECT id FROM _leadx_reset_missions)
    AND (t.status = ANY(p_status_allowlist) OR t.status = 'blocked')
  ON CONFLICT DO NOTHING;
  SELECT count(*) INTO v_task_count FROM _leadx_reset_tasks;

  INSERT INTO _leadx_reset_agent_runs(id)
  SELECT t.agent_run_id
  FROM public.agent_mission_tasks t
  WHERE t.id IN (SELECT id FROM _leadx_reset_tasks)
    AND t.agent_run_id IS NOT NULL
  ON CONFLICT DO NOTHING;
  SELECT count(*) INTO v_agent_run_count FROM _leadx_reset_agent_runs;

  INSERT INTO _leadx_reset_reservations(id)
  SELECT qr.id
  FROM public.quota_reservations qr
  WHERE qr.status = 'reserved'
    AND (qr.job_id IN (SELECT id FROM _leadx_reset_jobs)
      OR qr.operation_id IN (
        SELECT jo.id FROM public.job_operations jo
        WHERE jo.job_id IN (SELECT id FROM _leadx_reset_jobs)))
  ORDER BY qr.id
  FOR UPDATE;
  SELECT count(*) INTO v_reservation_count FROM _leadx_reset_reservations;
  SELECT count(*) INTO v_mission_count
  FROM public.agent_missions m
  WHERE m.id IN (SELECT id FROM _leadx_reset_missions)
    AND m.status IN ('queued','running');

  -- Keep report detail bounded while retaining compatibility totals above.
  v_selected_by_status := jsonb_build_object(
    'pipeline_runs', COALESCE((SELECT jsonb_object_agg(status, LEAST(row_count, v_report_limit)) FROM (
      SELECT pr.status, count(*)::integer AS row_count FROM public.pipeline_runs pr
      WHERE pr.id IN (SELECT id FROM _leadx_reset_runs) GROUP BY pr.status) s), '{}'::jsonb),
    'jobs', COALESCE((SELECT jsonb_object_agg(status, LEAST(row_count, v_report_limit)) FROM (
      SELECT j.status, count(*)::integer AS row_count FROM public.jobs j
      WHERE j.id IN (SELECT id FROM _leadx_reset_jobs) GROUP BY j.status) s), '{}'::jsonb),
    'agent_runs', COALESCE((SELECT jsonb_object_agg(status, LEAST(row_count, v_report_limit)) FROM (
      SELECT ar.status, count(*)::integer AS row_count FROM public.agent_runs ar
      WHERE ar.id IN (SELECT id FROM _leadx_reset_agent_runs) GROUP BY ar.status) s), '{}'::jsonb),
    'missions', COALESCE((SELECT jsonb_object_agg(status, LEAST(row_count, v_report_limit)) FROM (
      SELECT m.status, count(*)::integer AS row_count FROM public.agent_missions m
      WHERE m.id IN (SELECT id FROM _leadx_reset_missions) GROUP BY m.status) s), '{}'::jsonb),
    'mission_tasks', COALESCE((SELECT jsonb_object_agg(status, LEAST(row_count, v_report_limit)) FROM (
      SELECT t.status, count(*)::integer AS row_count FROM public.agent_mission_tasks t
      WHERE t.id IN (SELECT id FROM _leadx_reset_tasks) GROUP BY t.status) s), '{}'::jsonb),
    'quota_reservations', COALESCE((SELECT jsonb_object_agg(status, LEAST(row_count, v_report_limit)) FROM (
      SELECT qr.status, count(*)::integer AS row_count FROM public.quota_reservations qr
      WHERE qr.id IN (SELECT id FROM _leadx_reset_reservations) GROUP BY qr.status) s), '{}'::jsonb)
  );

  IF NOT p_apply THEN
    v_skipped_by_table := jsonb_build_object(
      'pipeline_runs', LEAST(v_run_count, v_report_limit), 'jobs', LEAST(v_job_count, v_report_limit),
      'agent_runs', LEAST(v_agent_run_count, v_report_limit), 'missions', LEAST(v_mission_count, v_report_limit),
      'mission_tasks', LEAST(v_task_count, v_report_limit), 'quota_reservations', LEAST(v_reservation_count, v_report_limit));
    v_skipped_reason_codes := '["dry_run"]'::jsonb;
    RETURN jsonb_build_object(
      'ok', true, 'applied', false, 'scope_kind', v_scope,
      'selected', jsonb_build_object('pipeline_runs',LEAST(v_run_count,v_report_limit),'jobs',LEAST(v_job_count,v_report_limit),
        'agent_runs',LEAST(v_agent_run_count,v_report_limit),'missions',LEAST(v_mission_count,v_report_limit),
        'mission_tasks',LEAST(v_task_count,v_report_limit),'quota_reservations',LEAST(v_reservation_count,v_report_limit)),
      'counts', jsonb_build_object('selected_by_status', v_selected_by_status,
        'transitioned_by_table', jsonb_build_object('pipeline_runs',0,'jobs',0,'agent_runs',0,'missions',0,'mission_tasks',0,'quota_reservations',0),
        'skipped_by_table', v_skipped_by_table, 'remaining', jsonb_build_object('active',0,'stale',0)),
      'transitioned', 0, 'accounting_pending', 0,
      'pending_reconciliation', jsonb_build_object('status','not_required','count',0,'reason_codes',v_failed_reason_codes),
      'skipped_reason_codes', v_skipped_reason_codes, 'failed_reason_codes', v_failed_reason_codes,
      'remaining_active', 0, 'remaining_stale', 0, 'ready_for_fresh_run', false);
  END IF;

  UPDATE public.pipeline_runs pr
  SET status = 'failed',
      error_message = 'scoped_reset',
      finished_at = now(),
      meta = COALESCE(pr.meta, '{}'::jsonb) || jsonb_build_object(
        'stage','failed', 'message','Stopped by scoped reset',
        'recovery', jsonb_build_object(
          'reason','scoped_reset', 'outcome','cancelled',
          'source_run_id',pr.id::text, 'terminal',true
        )
      )
  WHERE pr.id IN (SELECT id FROM _leadx_reset_runs)
    AND pr.status = ANY(p_status_allowlist);
  GET DIAGNOSTICS v_run_changed = ROW_COUNT;
  v_transitioned := v_run_changed;

  UPDATE public.jobs j
  SET status = 'cancelled', error = 'scoped_reset', error_category = 'scoped_reset',
      dependency_outcome = 'terminal',
      terminal_result = jsonb_build_object(
        'status','cancelled', 'reason','scoped_reset',
        'recovery', jsonb_build_object('source_job_id',j.id::text, 'outcome','cancelled', 'terminal',true)
      ),
      finished_at = now(), locked_by = NULL, locked_at = NULL,
      lease_expires_at = NULL, lease_renewed_at = NULL, retry_after = NULL,
      accounting_reconciliation_status = CASE
        WHEN EXISTS (SELECT 1 FROM public.job_operations jo WHERE jo.job_id = j.id)
        THEN 'pending' ELSE COALESCE(j.accounting_reconciliation_status, 'not_required') END
  WHERE j.id IN (SELECT id FROM _leadx_reset_jobs)
    AND j.status = ANY(p_status_allowlist);
  GET DIAGNOSTICS v_job_changed = ROW_COUNT;
  v_changed := v_job_changed;
  v_transitioned := v_transitioned + v_changed;

  UPDATE public.agent_runs ar
  SET status = 'cancelled', error = 'scoped_reset', finished_at = now(), updated_at = now()
  WHERE ar.id IN (SELECT id FROM _leadx_reset_agent_runs)
    AND ar.status IN ('queued','running');
  GET DIAGNOSTICS v_agent_run_changed = ROW_COUNT;
  v_changed := v_agent_run_changed;
  v_transitioned := v_transitioned + v_changed;

  UPDATE public.agent_mission_tasks t
  SET status = 'cancelled', error = 'scoped_reset', error_category = 'scoped_reset',
      dependency_outcome = 'terminal', terminal_result = jsonb_build_object('status','cancelled','reason','scoped_reset'),
      lease_owner = NULL, lease_expires_at = NULL, lease_renewed_at = NULL,
      run_after = now(), finished_at = now(), updated_at = now()
  WHERE t.id IN (SELECT id FROM _leadx_reset_tasks)
    AND (t.status = ANY(p_status_allowlist) OR t.status = 'blocked');
  GET DIAGNOSTICS v_task_changed = ROW_COUNT;
  v_changed := v_task_changed;
  v_transitioned := v_transitioned + v_changed;

  UPDATE public.agent_missions m
  SET status = 'cancelled', completed_at = now(), updated_at = now(),
      failure_summary = jsonb_build_object('reason','scoped_reset'),
      terminal_result = jsonb_build_object('status','cancelled','reason','scoped_reset')
  WHERE m.id IN (SELECT id FROM _leadx_reset_missions)
    AND m.status IN ('queued','running');
  GET DIAGNOSTICS v_mission_changed = ROW_COUNT;
  v_changed := v_mission_changed;
  v_transitioned := v_transitioned + v_changed;

  -- Finish the current attempt rows without deleting their evidence. The
  -- owner/attempt identity remains available for diagnostics and late CAS
  -- completion cannot match the reset terminal status.
  UPDATE public.job_attempts ja
  SET finished_at = COALESCE(finished_at, now()), outcome = 'cancelled',
      retryable = false, error_category = 'scoped_reset', error = 'scoped_reset'
  WHERE ja.job_id IN (SELECT id FROM _leadx_reset_jobs) AND ja.finished_at IS NULL;
  UPDATE public.agent_mission_task_attempts ta
  SET finished_at = COALESCE(finished_at, now()), outcome = 'cancelled',
      retryable = false, error_category = 'scoped_reset', error = 'scoped_reset'
  WHERE ta.task_id IN (SELECT id FROM _leadx_reset_tasks) AND ta.finished_at IS NULL;

  -- Reuse the established job-operation settlement path. A missing operation
  -- is a legacy row, not an accounting failure; an unavailable dependency is
  -- reported pending rather than claimed as reconciled.
  FOR v_job_id IN SELECT id FROM _leadx_reset_jobs LOOP
    BEGIN
      v_result := public.leadx_finish_job_operation(
        v_job_id, 'cancelled', jsonb_build_object('status','cancelled','reason','scoped_reset'));
      IF (v_result ? 'reason') AND v_result->>'reason' = 'job_operation_not_found' THEN
        UPDATE public.jobs SET accounting_reconciliation_status = 'not_required'
        WHERE id = v_job_id AND status = 'cancelled';
      ELSIF COALESCE(v_result->>'accounting_status','') IN ('released','none')
         OR (v_result ? 'outcome') AND v_result->>'outcome' = 'replay' THEN
        UPDATE public.jobs SET accounting_reconciliation_status = 'reconciled'
        WHERE id = v_job_id AND status = 'cancelled';
      ELSE
        v_accounting_pending := v_accounting_pending + 1;
        IF NOT (v_failed_reason_codes ? 'job_accounting_pending') THEN
          v_failed_reason_codes := v_failed_reason_codes || '["job_accounting_pending"]'::jsonb;
        END IF;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      v_accounting_pending := v_accounting_pending + 1;
      IF NOT (v_failed_reason_codes ? 'accounting_dependency_unavailable') THEN
        v_failed_reason_codes := v_failed_reason_codes || '["accounting_dependency_unavailable"]'::jsonb;
      END IF;
    END;
  END LOOP;

  FOR v_reservation_id IN SELECT id FROM _leadx_reset_reservations LOOP
    BEGIN
      v_result := public.leadx_record_quota_settlement(
        v_reservation_id,
        (SELECT tenant_id FROM public.quota_reservations WHERE id = v_reservation_id),
        'scoped-reset:' || v_reservation_id::text,
        'released',
        (SELECT GREATEST(COALESCE(reserved_quantity,0) - COALESCE(committed_quantity,0),0)
         FROM public.quota_reservations WHERE id = v_reservation_id));
      IF NOT COALESCE((v_result->>'ok')::boolean, false) THEN
        v_accounting_pending := v_accounting_pending + 1;
        IF NOT (v_failed_reason_codes ? 'quota_settlement_pending') THEN
          v_failed_reason_codes := v_failed_reason_codes || '["quota_settlement_pending"]'::jsonb;
        END IF;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      v_accounting_pending := v_accounting_pending + 1;
      IF NOT (v_failed_reason_codes ? 'quota_accounting_unavailable') THEN
        v_failed_reason_codes := v_failed_reason_codes || '["quota_accounting_unavailable"]'::jsonb;
      END IF;
    END;
  END LOOP;

  SELECT count(*) INTO v_remaining_active FROM public.pipeline_runs pr
  WHERE pr.id IN (SELECT id FROM _leadx_reset_runs) AND pr.status = ANY(p_status_allowlist);
  SELECT v_remaining_active + count(*) INTO v_remaining_active FROM public.jobs j
  WHERE j.id IN (SELECT id FROM _leadx_reset_jobs) AND j.status = ANY(p_status_allowlist);
  SELECT v_remaining_active + count(*) INTO v_remaining_active FROM public.agent_runs ar
  WHERE ar.id IN (SELECT id FROM _leadx_reset_agent_runs) AND ar.status IN ('queued','running');
  SELECT v_remaining_active + count(*) INTO v_remaining_active FROM public.agent_mission_tasks t
  WHERE t.id IN (SELECT id FROM _leadx_reset_tasks) AND (t.status = ANY(p_status_allowlist) OR t.status = 'blocked');
  SELECT v_remaining_active + count(*) INTO v_remaining_active FROM public.agent_missions m
  WHERE m.id IN (SELECT id FROM _leadx_reset_missions) AND m.status IN ('queued','running');
  v_remaining := v_remaining_active;
  -- A stale scope has no separate hidden queue: every remaining active row is stale.
  v_remaining_stale := CASE WHEN v_scope = 'tenant_stale' THEN v_remaining_active ELSE 0 END;
  v_transitioned_by_table := jsonb_build_object(
    'pipeline_runs', LEAST(v_run_changed, v_report_limit), 'jobs', LEAST(v_job_changed, v_report_limit),
    'agent_runs', LEAST(v_agent_run_changed, v_report_limit), 'missions', LEAST(v_mission_changed, v_report_limit),
    'mission_tasks', LEAST(v_task_changed, v_report_limit), 'quota_reservations', 0);
  v_skipped_by_table := jsonb_build_object(
    'pipeline_runs', LEAST(GREATEST(v_run_count - v_run_changed, 0), v_report_limit),
    'jobs', LEAST(GREATEST(v_job_count - v_job_changed, 0), v_report_limit),
    'agent_runs', LEAST(GREATEST(v_agent_run_count - v_agent_run_changed, 0), v_report_limit),
    'missions', LEAST(GREATEST(v_mission_count - v_mission_changed, 0), v_report_limit),
    'mission_tasks', LEAST(GREATEST(v_task_count - v_task_changed, 0), v_report_limit),
    'quota_reservations', 0);
  IF v_skipped_by_table <> '{"pipeline_runs": 0, "jobs": 0, "agent_runs": 0, "missions": 0, "mission_tasks": 0, "quota_reservations": 0}'::jsonb
     AND NOT (v_skipped_reason_codes ? 'status_changed_before_transition') THEN
    v_skipped_reason_codes := v_skipped_reason_codes || '["status_changed_before_transition"]'::jsonb;
  END IF;

  RETURN jsonb_build_object(
    'ok', v_accounting_pending = 0, 'applied', true, 'scope_kind', v_scope,
    'selected', jsonb_build_object('pipeline_runs',LEAST(v_run_count,v_report_limit),'jobs',LEAST(v_job_count,v_report_limit),
      'agent_runs',LEAST(v_agent_run_count,v_report_limit),'missions',LEAST(v_mission_count,v_report_limit),
      'mission_tasks',LEAST(v_task_count,v_report_limit),'quota_reservations',LEAST(v_reservation_count,v_report_limit)),
    'counts', jsonb_build_object('selected_by_status', v_selected_by_status,
      'transitioned_by_table', v_transitioned_by_table, 'skipped_by_table', v_skipped_by_table,
      'remaining', jsonb_build_object('active',LEAST(v_remaining_active,v_report_limit),
        'stale',LEAST(v_remaining_stale,v_report_limit))),
    'transitioned', LEAST(v_transitioned, v_report_limit), 'accounting_pending', v_accounting_pending,
    'pending_reconciliation', jsonb_build_object(
      'status', CASE WHEN v_accounting_pending = 0 THEN 'reconciled' ELSE 'pending' END,
      'count', LEAST(v_accounting_pending, v_report_limit), 'reason_codes', v_failed_reason_codes),
    'skipped_reason_codes', v_skipped_reason_codes, 'failed_reason_codes', v_failed_reason_codes,
    'remaining_active', LEAST(v_remaining_active,v_report_limit),
    'remaining_stale', LEAST(v_remaining_stale,v_report_limit),
    'ready_for_fresh_run', v_remaining_active = 0 AND v_accounting_pending = 0);
END;
$$;

REVOKE ALL ON FUNCTION public.leadx_transition_scoped_work(text,uuid,uuid,timestamptz,text[],boolean)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.leadx_transition_scoped_work(text,uuid,uuid,timestamptz,text[],boolean)
  TO service_role;

NOTIFY pgrst, 'reload schema';
