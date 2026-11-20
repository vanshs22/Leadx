-- Outreach guardrails: approval mode (auto_send off by default), EU/UK exclusion (on by default), lawful-basis note.
-- Additive and idempotent. Existing campaigns switch to approval mode, which is the safe default.
ALTER TABLE IF EXISTS public.autonomous_campaigns
  ADD COLUMN IF NOT EXISTS auto_send boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS exclude_eu_uk boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS lawful_basis_note text;

-- email_sends.provider_id also stores the SMTP Message-ID; IMAP reply matching looks it up.
CREATE INDEX IF NOT EXISTS idx_email_sends_provider_id ON public.email_sends (provider_id) WHERE provider_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_sends_to_email ON public.email_sends (to_email);

NOTIFY pgrst, 'reload schema';
