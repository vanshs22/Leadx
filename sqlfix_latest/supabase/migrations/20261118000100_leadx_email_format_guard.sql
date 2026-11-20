-- Last line of defence for the "TikTok URL saved as an email" class of bug. The application validates before writing; this makes the
-- database refuse such values even if a future code path forgets to. NOT VALID: existing rows are not scanned or rejected, new writes are checked.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['enrichment', 'leads_global'] LOOP
    IF to_regclass('public.' || t) IS NOT NULL
       AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = t AND column_name = 'email')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = t || '_email_format_chk') THEN
      EXECUTE format($f$ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (
        email IS NULL OR (
          email ~* '^[a-z0-9._%%+\-]{1,64}@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,24}$'
          AND email !~* '(tiktok|instagram|facebook|linkedin|twitter|youtube|pinterest)\.com'
          AND email !~* '@[0-9]x\.'
          AND email !~* '\.(png|jpe?g|gif|webp|svg|css|js)$'
        )) NOT VALID$f$, t, t || '_email_format_chk');
    END IF;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
