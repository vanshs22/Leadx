-- Keep delivered_at truthful: assignment creation is not delivery.
-- Pending/failed assignments have never reached a successful delivery state.
UPDATE public.lead_assignments
SET delivered_at = NULL
WHERE delivery_status IN ('pending', 'failed')
  AND delivered_at IS NOT NULL;

COMMENT ON COLUMN public.lead_assignments.delivered_at IS
  'Timestamp of successful delivery confirmation; NULL while assignment is pending or failed.';
