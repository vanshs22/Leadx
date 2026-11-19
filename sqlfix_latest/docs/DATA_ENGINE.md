# LeadX data engine

How leads are found, enriched, gated, scored, assigned and delivered, what is free and what costs credits, and what has and has not been verified.

## Flow

```
inventory (leads_global, other tenants' enriched leads)  ->  skip if it covers the target with headroom
free discovery (osm lane)  Overture | NPPES | Companies House | directories | OSM   merged by entity_resolution
   -> suppression + opt-out -> skip leads already in the tenant CRM -> persist (leads_global, batch upsert)
   -> free enrichment: local fetch (Scrapling/httpx), contact pages, typed extraction (field_extract)
   -> Firecrawl only if the page is unrendered and no contact was found  -> paid providers (ordered by hits per credit)
   -> local verification (syntax, MX, validator, SMTP probe) -> score (fit / reachability / timing) -> gate
   -> good fit but no contact: review queue (free recovery)      -> rejected otherwise
   -> final verification (paid verifier only now) -> assign (reserve/commit/release) -> CRM projection -> delivery outbox
```

## Free discovery sources (`app/integrations/prod/free_discovery.py`)

| Source | Module | Coverage | Notes |
|---|---|---|---|
| Overture Maps Places | `open_data.py` | global, phone/website/email/socials | DuckDB over public S3 parquet, latest release from the STAC catalog. Treat contact fields as hints. |
| NPPES | `registries.py` | US healthcare organisations | Phone plus the authorised official (owner name and title). Verticals: dental, wellness. |
| Companies House | `registries.py` | UK companies | Needs `COMPANIES_HOUSE_API_KEY`. Written and unit-level only; not run against the live API. |
| Directories | `directory_discovery.py`, `directory_extract.py` | per country | Listing pages are paged until a page adds nothing new. YellowPages, Superpages, Hotfrog parse fully; BBB is blocked by default (403). |
| Web search | `web_search.py` | any | SearXNG first, every page until no new URL; Serper only if SearXNG returns nothing. Also feeds social profile search and per-lead contact lookup. |
| OSM | `exhaustive_discovery.py` | global | Overpass, falling back to Nominatim. |

### Source tiers

| Tier | Sources | Default | Why |
|---|---|---|---|
| Open data | Overture Maps, OSM (Overpass mirrors via `OVERPASS_URLS`, Nominatim fallback) | on | Licensed or open, no scraping. |
| Registries | NPPES (US healthcare), Companies House (UK) | on | Official public APIs. |
| Working directories | yellowpages.com, superpages.com, hotfrog.com, dexknows.com (plus merchantcircle, yell, 192, europages and regional ones by country) | on | Return 200 to a polite, robots-respecting client. |
| Blocked by default | bbb.org, manta.com, brownbook.net, chamberofcommerce.com, cylex.us.com, yelp.com | off | Return 401/403 to automated clients or disallow crawling. They are skipped with zero requests instead of tripping the breaker every run. Re-enable one with `LEADX_DIRECTORY_POLICY='{"bbb.org": {"enabled": true}}'` once you have approved access or your own proxy. |
| Official API lanes | Yelp Fusion, Foursquare, Google Places | off until a key is set | Licensed APIs give the same businesses without scraping; use these instead of the blocked directories. |

`LEADX_DIRECTORY_PROXY` takes one plain egress proxy URL for your own paid proxy account. There is no rotation, no CAPTCHA solving and no fingerprint evasion; if a domain still refuses through the proxy it stays skipped.

### Streaming flow

`discover_free(..., on_rows=cb)` awaits `cb` with the new merged entities each time a source finishes (and every `LEADX_FREE_STREAM_TICK` seconds for sources that report partial rows). The pipeline's `_fast_seed` passes a callback that queues rows to enrichment immediately, so crawl, verification, scoring, gating and assignment run for early leads while slower sources are still searching. Later evidence for an already emitted entity is not re-emitted. `LEADX_FAST_SEED=0` disables the seed. Run telemetry records `first_candidate_seconds`, `first_enriched_seconds` and `first_assigned_seconds`.

### What verification works here

Output of `python scripts/check_verification.py` from the sandbox (no keys):

| Check | Works | Detail |
|---|---|---|
| DNS MX lookup | yes | gmail.com has 5 MX records; a missing domain returns none |
| Disposable and role mailbox block | yes | mailinator.com and postmaster@ rejected |
| SMTP mailbox probe | no, needs port 25 | outbound 25 is blocked, result is `unknown`; Microsoft/Yahoo MX cannot be probed from any host |
| Phone format and region (phonenumbers) | yes | valid and invalid US numbers classified |
| Phone line is live | no, needs a key | local check proves format only |

Mailbox-level email verification and live phone checks need a verifier key (below) or a host with port 25 open.

### Paid fallbacks and environment variables

All paid lanes are off until a key is set, run cheapest first, and only for leads about to be assigned. Spend is written to `enrichment_spend_ledger`. Set keys in the environment only, never in files.

| Variable | Purpose |
|---|---|
| `LEADX_PAID_FALLBACK` | `auto` (default: paid only when free lanes left a gap), `always`, `never` |
| `LEADX_PAID_VERIFY` | `delivery` (default: paid verify only at final assignment), `never` |
| `SERPER_API_KEY` | Serper web search, used after SearXNG returns nothing and for owner-domain lookups |
| `FIRECRAWL_API_KEY` | Firecrawl rendering; with `LEADX_FIRECRAWL_MODE=fallback` it runs only when the page is unrendered and no contact was found |
| `HUNTER_API_KEY`, `ZEROBOUNCE_API_KEY`, `ABSTRACT_API_KEY` | Email verifiers, free plan or paid (tier 1) |
| `MILLIONVERIFIER_API_KEY`, `EMAILLISTVERIFY_API_KEY`, `REOON_API_KEY` | Cheap paid email verifiers (tier 2, tried first) |
| `NEVERBOUNCE_API_KEY`, `BOUNCER_API_KEY`, `KICKBOX_API_KEY` | Other paid email verifiers (tier 2) |
| `LEADX_EMAIL_VALIDATOR_URL` | Your own validator endpoint |
| `PHONEVALIDATIONAPI_KEY`, `NUMBERS_ONLINE_API_KEY` | Free-plan phone verifiers (tier 0) |
| `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN`, `VERIPHONE_API_KEY`, `ABSTRACT_API_KEY`, `NUMVERIFY_API_KEY` | Phone verifiers (tier 1) |
| `TELNYX_API_KEY`, `IPQS_API_KEY` | Paid phone lookup (tier 2) |
| `FOURSQUARE_API_KEY` (or `FSQ_API_KEY`) | Foursquare places discovery |
| `GOOGLE_PLACES_API_KEY` | Google Places discovery |
| `YELP_API_KEY` | Yelp Fusion discovery |
| `LEADX_DIRECTORY_PROXY` | One egress proxy URL for directory fetches (your own paid proxy) |
| `LEADX_DIRECTORY_POLICY` | JSON per domain: `enabled`, `robots`, `min_interval` |
| `OVERPASS_URLS` | Comma list of Overpass mirrors |
| `LEADX_HTTP_UA` | User-Agent for open-data and directory requests |

Directory controls: `LEADX_DIRECTORY_SCRAPING=0` kill switch, robots.txt respected by default (`LEADX_DIRECTORY_ROBOTS`, per-domain override in `LEADX_DIRECTORY_POLICY`), per-domain spacing, circuit breaker after 3 block responses, no CAPTCHA solving. Blocked domains are listed under Source tiers; they are skipped, not bypassed.

`bing_search` was removed (the Bing Search API is retired).

## Extraction (`field_extract.py`)

Typed extractors for email, phone, social, link hubs, people, address and JSON-LD business data, each with a validator and the page it came from. Rejects platform/placeholder domains, image filenames, no-reply addresses and unknown TLDs; decodes Cloudflare-protected and `[at]` addresses. Persistence normalises with `utils.normalize_email`, and the database refuses malformed emails (`*_email_format_chk`, migration 47).

Quality check: `tests/test_extraction_golden.py` runs 100 synthetic pages (precision/recall floors). The pages were written by the same author as the extractor, so this guards regressions; it does not measure real-world accuracy. `scripts/benchmark_extraction.py` compares site extraction with Overture values for real businesses (see results below).

## Website acquisition engine (`fetch_engine.py`, `fetch_classify.py`, `alt_recovery.py`, `acquisition.py`)

Every page fetch (`enricher.fetch_scrapling`, so enrich_one, the enrichment mesh, directories and the website finder) goes through `fetch_engine.acquire(url)` unless `LEADX_FETCH_ENGINE=0`.

```
direct HTTP (pooled httpx, per-hop SSRF checks, free same-site fixes) -> classify the answer (fetch_classify)
  usable page                      -> done
  JS shell / redirect stub / empty -> local browser (Playwright; Crawl4AI if installed) -> Firecrawl (only with a key, never when LEADX_PAID_FALLBACK=never)
  disconnect / 5xx / tls / connect -> Scrapling and browser, ordered by observed success per cost
  challenge, 403, 429              -> STOP: classify, keep prior data, cool the domain down, queue for a person, run alternative sources
  dead domain / parked / 404 site  -> STOP, run alternative sources
alternative sources (alt_recovery): discovery hints -> Internet Archive snapshot -> YellowPages/Superpages card -> search snippets (SearXNG/Serper) -> Google Places -> successor site (dead domains only); Nominatim is opt-in
```

- **Classification** (`fetch_classify.Verdict`): ok, js_shell, redirect_stub, parked; challenges cloudflare, recaptcha, hcaptcha, datadome, perimeterx, sgcaptcha (SiteGround), akamai, incapsula, sucuri, js_challenge, access_denied, http_403, http_429; http_404/4xx/5xx, empty_body; dns, timeout, tls, connect, disconnect, too_many_redirects. Captcha/challenge marker strings count only on a response that is an error status or a small, almost empty page with no mailto/tel link, because real pages embed captcha widgets on contact forms.
- **Provider choice** (`Engine._next`): candidates for that verdict are ranked by smoothed success probability divided by cost and latency weight, from history per (provider, triggering verdict) and per (provider, domain). A provider that failed twice on a domain with no success is skipped for it. History is seeded from `fetch_attempts` (`fetch_telemetry.seed_history`) and from priors measured in the audit.
- **Controls**: per-provider concurrency (browser 4, Firecrawl 4, Scrapling 16), per-provider timeout, per-URL budget (`LEADX_FETCH_BUDGET_S`), per-host limit of 3, circuit breaker per provider (opens after 5 provider faults such as a missing browser, never on a site's refusal), 5 minute URL result cache, shared hosting platforms (glossgenius, square.site, ...) are tracked per subdomain.
- **Challenges**: nothing here solves, evades or retries a challenge through another fetcher. The domain gets a cooldown (challenge 6 h, 429 15 min, 403 1 h, DNS/404/parked 24 h), the outcome row is `needs_human` with `human_status=pending`, and `enrichment_layer` adds a `lead_review_queue` entry (`site_challenge:<vendor>`). An operator opens the site by hand and marks it resolved, dismissed or retry (retry clears the cooldown). The lead keeps all earlier data and `enrichment_ok` is only false when no source produced anything; the error is `site_challenge:<vendor>`.
- **Alternative-source identity** (`alt_recovery.identity_match`): a candidate is accepted only with a shared domain, phone or street address (strong signals) plus a compatible business name, or two strong signals. Name alone never merges, and a different phone or ZIP rejects it even when the names match. Rejections are returned with the reason. Vendor and agency emails in archived footers are dropped (`_own_or_freemail`).
- **Acquisition classes** (`acquisition.py`): fetched, recovered (alternative source gave an email/phone the lead lacked), corroborated (alternative source confirmed the discovery contact), hint_only, failed. They are reported separately everywhere; website fetch success is never mixed with recovery.
- **Extraction and provenance**: `content_prep.extract_profile` adds description, services, industries, company size and technologies deterministically (JSON-LD, meta tags, headings, script signatures). `field_provenance` records value, source, method, URL, confidence, observed_at and status per field: `verified` (first-party structured markup, or two independent sources agree), `inferred` (plain text or one third-party source), `hint` (discovery record, not re-observed).
- **Tokens and cost**: the acquisition path calls no LLM, so tokens are 0. `content_prep.main_text` (boilerplate removal), `content_hash`, the URL cache, the cooldown and the corroborating-source rule keep work off any later model; `needs_llm()` flags the leads where deterministic extraction left an owner gap despite enough clean text, so the size of that need is measurable. Per-lead cost is the sum of Firecrawl/Places credits in `fetch_outcomes.cost`.
- **Telemetry and dashboard**: tables `fetch_attempts`, `fetch_outcomes` (migration `20261119000100`). `GET /crm/{t}/engine/fetching` (filters `date_from`, `date_to`, `provider`, `category`, `source`) returns attempted/successful/blocked/HTTP/timeout counts, recoveries, completeness, per-provider success, average fetch time, cost per enriched lead, tokens and problem domains. `GET .../engine/fetching/verification` and `POST .../engine/fetching/verification/{id}` (resolved | dismissed | retry) drive the human queue. Portal page: `/fetching`.
- **Audit and benchmark**: `python scripts/fetch_audit.py collect|audit|bench|report`; results and the honest reading are in [FETCH_AUDIT.md](FETCH_AUDIT.md).

### Fetching environment variables

| Variable | Default | Effect |
|---|---|---|
| `LEADX_FETCH_ENGINE` | 1 | 0 restores the previous direct-then-Scrapling path and disables alternative-source recovery |
| `LEADX_FETCH_PROVIDERS` | direct,scrapling,browser,crawl4ai,firecrawl | providers the engine may use |
| `LEADX_FETCH_BROWSER` / `LEADX_FETCH_BROWSER_CONCURRENCY` | 1 / 4 | local Playwright Chromium (needs `playwright install chromium`) |
| `LEADX_FETCH_HOST_CONCURRENCY` | 3 | simultaneous requests per site |
| `LEADX_FETCH_BUDGET_S` | 60 | total time per URL across providers |
| `LEADX_FETCH_CACHE_SECONDS` | 300 | URL result cache |
| `LEADX_FETCH_COOLDOWN_SCALE` | 1.0 | multiplier on domain cooldowns |
| `LEADX_FETCH_PLATFORM_DOMAINS` | empty | extra multi-tenant hosting domains tracked per subdomain |
| `LEADX_ALT_RECOVERY` | 1 | alternative-source recovery on/off |
| `LEADX_ALT_SOURCES` | hints,archive,directory,search,places | order of alternative sources (add `osm` for Nominatim, 1 request/s) |
| `LEADX_ALT_DEADLINE_S` | 40 | most time recovery may spend per lead |
| `LEADX_ALT_ARCHIVE_MAX_AGE_DAYS` | 1825 | oldest Internet Archive snapshot accepted |
| `LEADX_NOMINATIM_URL`, `LEADX_NOMINATIM_INTERVAL` | public instance, 1.1 | Nominatim endpoint and spacing |
| `GOOGLE_PLACES_API_KEY`, `LEADX_PLACES_CREDITS` | unset, 0.017 | official Places text search (paid, only with a key) |
| `FIRECRAWL_API_KEY(S)`, `FIRECRAWL_CREDITS_PER_PAGE` | unset, 1 | Firecrawl as a rendering provider; never used when `LEADX_PAID_FALLBACK=never`; not a captcha solver |
| `SEARXNG_URL`, `SERPER_API_KEY(S)` | unset | search-snippet source (existing `web_search`) |

## Waterfall and spend (`waterfall_runtime.py`, `enrichment_mesh.py`)

- Providers are ordered by expected hits per credit from `ProviderIntelligence` (priors keep the historical order until there is data).
- A provider that missed a field for a lead is skipped until the field's TTL passes (email 30 d, phone 90 d, owner 120 d; `LEADX_FIELD_TTL_JSON`).
- `LEADX_RUN_CREDIT_CAP` and `lead_policy.run_credit_cap` cap paid credits per run.
- Every paid attempt (providers, Firecrawl, paid email verification) is written to `enrichment_spend_ledger`.
- Firecrawl is a fallback (`LEADX_FIRECRAWL_MODE=fallback`): only when no contact was found after the free crawl and the page looks unrendered. `always` restores the old behaviour.
- Paid email verifiers run only for the lead about to be assigned (`LEADX_PAID_VERIFY=delivery`, `always` or `never`).

## Per-tenant policy (`tenants.settings.lead_policy`, `GET/PUT /crm/{tenant}/engine/policy`)

`contact_requirement` email | phone | either, `verified_email_only`, `run_credit_cap`, `skip_known_crm`.

## Review queue, delivery, metrics (`engine_api.py`)

- `GET /crm/{t}/engine/review`, `POST .../review/{id}/resolve` (approve | reject), `POST .../review/{id}/recover` (free re-enrichment + verification).
- `GET /crm/{t}/engine/delivery` (outbox state, dead letters), `POST .../delivery/{id}/replay`.
- `GET /crm/{t}/engine/fetching`, `.../fetching/verification`: fetching intelligence (see Website acquisition engine).
- `GET /crm/{t}/engine/metrics`: coverage by field, accuracy (share not credited back), credits per assigned lead and per provider, time to delivery, review counts.

## Scoring

`score_lead_detailed` returns the score plus `fit`, `reachability`, `timing` (0-100) and the top three reasons; the reason string stored in `scores.reasoning` starts with them.

## Cross-tenant reuse

Discovered leads live once in `leads_global`; enrichment is shared across tenants for `ENRICHMENT_REUSE_DAYS`. Inventory-first now decides: when inventory holds at least 1.5x the target (`LEADX_INVENTORY_HEADROOM`), it is admitted and drained before live discovery runs (`LEADX_INVENTORY_FIRST_AUTHORITATIVE=0` turns this off). A tenant never receives a lead it already has (assignments, `tenant_seen_leads`, CRM companies/contacts, suppression), and 90-day cooldown and exclusivity checks still apply at assignment.

## Operations

```
python scripts/apply_supabase_schema.py --reset --confirm-ref <ref>    # needs SUPABASE_DB_URL (database password)
python scripts/supabase_doctor.py                                      # keys, schema, RLS, auth, Realtime
python scripts/real_supabase_pipeline.py --industry dentist --location "Austin, TX" --target 10
python scripts/benchmark_extraction.py "Austin, TX" dentist 40
WITH_SEARXNG=1 scripts/local_stack/stack.sh python scripts/local_stack/e2e.py     # local stack, 84 API checks
```

## Verified, and not

Verified this pass: unit tests (`tests/test_free_discovery.py`, `test_waterfall_runtime.py`, `test_data_engine_policies.py`, `test_enricher_firecrawl_fallback.py`, golden extraction); 84 local API checks including the new engine endpoints; the free layer on live data (Austin dentists: 1,010 Overture rows, 539 NPPES organisations, 354 directory rows (with SearXNG), merged to 777 businesses, 590 with a website, 403 with an email); a real Supabase project: keys, Auth admin and password sign-in, private Realtime channel join for a member, join refused for another tenant, `crm_change` broadcast received, and a 3-lead pipeline run that completed (`target_met`) in about 4 minutes.

Not verified (fetch engine): Firecrawl, Serper/SearXNG search and Google Places (no keys or instance here; mocked in `tests/test_fetch_engine.py`), Crawl4AI (not installed), the new tables on a real Supabase project (exercised on the SQLite plane only); the portal page was typechecked and `next build` passes, but it was not opened against a live API.

Not verified: the real project's schema is behind this repository (no `tenants.settings`, no migration 48 tables), so the 10-lead run and the new engine tables on Supabase wait for the schema reset. HubSpot and Salesforce delivery against developer accounts (no credentials). Companies House live. Email deliverability beyond MX (port 25 is blocked, no paid verifier key). Phone liveness (no provider key). Serper (account has no credits).

Extraction benchmark (Austin dentists, 40 sites, homepage plus two contact pages): the site lists the same phone as Overture for 70% of businesses and the same email for 36%. The misses inspected were sites listing a different number (other location, tracking number) or JavaScript-only pages, so this is a disagreement rate against a source that can itself be stale, not an accuracy figure.

## Speed

Measured on the local stack (Postgres about 2.5 ms per call, which is what an API co-located with its database sees), Austin/Houston dentists, free sources only, no paid calls:

| Target | First lead assigned | All assigned | Run finished |
|---|---|---|---|
| 10 | 6 s | 7 s | 7 s |
| 100 | 6 s | 24 s | 25 s |
| 500 (Houston) | 11 s | 90 s | 101 s |

Against the real Supabase project from this sandbox (237 ms per call to Tokyo) the same 10-lead run takes about 100 s, because the pipeline makes roughly 50 sequential database calls per lead. Run the API and worker in the database's region; the numbers above are what to expect there. `scripts/local_stack/speed_probe.py` reproduces them (`SPEED_TARGET`, `SPEED_LOCATION`).

What changed:
- **Fast seed.** Before the normal discovery rounds, the run pulls Overture, NPPES and Companies House for every location in parallel (about 3 s), merges them, and feeds the pool to enrichment in chunks. Slower sources (directories, search) stay in the later rounds.
- **Corroborated leads skip the site crawl.** A lead with two independent sources, a website and the contact the tenant needs goes straight to verification (`LEADX_FAST_DELIVER=0` disables). Its `enrichment_method` is `multi_source_corroborated` and `enrichment_deferred` is set.
- **Enrichment concurrency** scales with the target (12 to 48 workers, was 3), the per-lead timeout is 20 s (was 45), in-flight crawls are dropped when the target is met, and over-admission is 4x (was 2x).
- **The ten-agent specialist stage no longer runs inline** (`LEADX_SPECIALIST_AGENTS=inline` restores it). It crawled up to 12 pages per lead, used Firecrawl, and read the whole `lead_assignments` table on the event loop for every call.
- **Event loop and database chatter.** Cached SSL contexts (building one per request took about 40 ms on the loop), a one-second cache for the job fence check, cached tenant weights, one write instead of two for each run-board card, batched stage history, and no more read before write for newly discovered leads. Database transactions per 100-lead run fell from about 18,000 to about 9,000.
- **Pipeline bugs that only show at speed:** the assignment queue deadlocked when Tier B leads were re-queued into a full queue; a run board over 500 cards was read with a 500-row cap that dropped the newest rows, so finished runs were reported short; the final counts could be read before in-flight assignment writes landed.
- `latency_samples` (p50/p90 per stage) are recorded in the run's `lane_telemetry`.

Not done: the remaining per-lead cost is Python database work in one process (about 0.18 s of CPU per lead). More throughput than the 500-in-90-seconds above needs sharding a run across worker processes.

## Security review (this pass)

Checked and fine: JWT verification (HS256 or JWKS, audience enforced, tokens now must carry `exp` and `sub`), tenant isolation on every new endpoint, RLS on all 219 public tables (`scripts/check_rls_coverage.py`), anon and authenticated roles have no table grants on the real project, Realtime private channels refuse non-members, SSRF guard on every outbound fetch (directory pages included), no secrets in the repository, CSRF and security headers.

Fixed:
- `python-jose` (open advisories, no patched release) replaced by PyJWT; `cryptography` 42 to 50, `python-dotenv`, `dnspython` upgraded. `pip-audit` on `requirements.txt` now reports no known vulnerabilities.
- `/docs`, `/redoc` and `/openapi.json` are disabled in production and staging (`LEADX_PUBLIC_API_DOCS=1` re-enables).
- Review resolve, recover and delivery replay need a member role or above (viewers are read-only); `recover` is rate limited because it crawls a website.
- Overture release string validated before it reaches the S3 path; MD5 fingerprints marked non-security.
- The portal's image optimizer is turned off (the portal does not use `next/image`) and HSTS is sent in hardened profiles. SearXNG runs on the internal compose network only, with a required `SEARXNG_SECRET`.

Open:
- **Next.js 14.2.35 has published advisories, some critical** (the SVG/AVIF image-optimization RCE, Server Actions DoS). Patched releases exist only in Next 16, which also needs React 19. The portal uses neither the image optimizer nor Server Actions, which is what the listed critical issues need, so this is mitigated but not closed. Plan the Next 16 upgrade and run the 50 browser checks against it.
- `scripts/check_migrations.py` still reports BLOCKED. It is the upgrade gate for a populated production database and needs operator-written recovery notes per risky migration; it does not apply to a fresh install such as the one just done.
- No global API rate limiter for authenticated routes; only sensitive endpoints are limited. Put a limit at the gateway.
- Email deliverability beyond MX and phone liveness are unverified without provider keys.
