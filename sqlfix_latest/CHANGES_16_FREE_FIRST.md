# LeadX 16 - free/local first, paid only as a fallback

## Policy (one switch: `LEADX_PAID_FALLBACK=auto|always|never`)
Every stage runs local/open sources first, then free-tier keys, then paid keys. A paid provider
runs only if (a) its key is set in env and (b) the earlier tiers left a gap. Shared logic lives in
`app/integrations/prod/paid_policy.py`.

## Verification (contact_verification_mesh.py)
- NEW `local_smtp_probe` (free, no key): MX -> `RCPT TO` (no mail sent) -> random control address to detect
  catch-all. Returns `valid` only for accepted + non-catch-all; `invalid` only for explicit "user unknown"
  (policy blocks / 5.7.x are `unknown`, never `invalid`). Skips Microsoft/Yahoo/gateway MX (they accept all),
  rate-limits per MX host, caches results, and pauses itself if port 25 is blocked.
- NEW `phone_local_check`: libphonenumber (installed) or NANP rules. Confidence 70, so configured phone
  providers still confirm the line is live.
- Providers (Hunter/ZeroBounce/Abstract/NeverBounce/Bouncer/Kickbox, phone APIs) are consulted only when the
  local result is not decisive. Canonical confidence is now the real best confidence, not a flat 95.
- `verification_readiness()` is now `ready=True` always and returns `warnings`; the pipeline / API / startup
  no longer refuse to run without a paid verifier (the 15.9 preflight is replaced by log warnings).

## Discovery
- `discover_multi_source` (the streaming-target path): new free OSM stage first; Serper lanes
  (maps/web/yelp/facebook) run only if free results are not enough.
- `run_core_discovery` (exhaustive path): takes `want`; Serper maps, connector catalog, native commercial APIs
  and the intensive-lane Serper gap-fill are skipped when free lanes already cover it.

## Enrichment
- Layer order: free mesh -> free deep crawl (contact/about/team pages) -> paid pass. Previously paid ran
  before the deep crawl.
- Paid pass only fills email/phone (`LEADX_PAID_ENRICH_FIELDS`), no longer revenue/funding/technologies for every lead.
- Serper contact search only when the crawl found no email (owner lookups opt-in).
- If a lead still has no verified contact after local verification, one paid refill + re-verify (`lead6_runtime`).

## Tests
`tests/test_leadx16_free_first.py` (13 tests, fake SMTP server, no network). Replaced the 15.9 test that
asserted the blocking preflight.
