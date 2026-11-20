-- Per-webhook signing secret. Deliveries were signed only with one global WEBHOOK_SIGNING_SECRET, and were unsigned when it was unset.
-- Existing rows get NULL and keep the legacy behaviour until the webhook is re-created; new webhooks always receive a secret.
ALTER TABLE IF EXISTS public.outbound_webhooks ADD COLUMN IF NOT EXISTS signing_secret text;

NOTIFY pgrst, 'reload schema';
