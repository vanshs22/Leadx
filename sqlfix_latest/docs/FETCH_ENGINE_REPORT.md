# Fetch engine: progress and system report

Details and tables: `docs/FETCH_AUDIT.md` (regenerate with `python scripts/fetch_audit.py report`). Raw data: `artifacts/fetch_audit/*.json`.
All numbers are from real sites, free path only (no provider keys, no LLM calls). Nothing here is estimated.

## 1. Progress on the 8 requested parts

| # | Part | Status |
|---|---|---|
| 1 | Audit and failure classification | Done. 302-site audit, per verdict, status, server header, domain, provider recovery. |
| 2 | Intelligent cascade (history-aware, breakers, concurrency, timeouts, failover) | Done for direct, Scrapling, local Playwright. Firecrawl, Serper/SearXNG, Google Places are coded behind env keys and covered by mocked tests only. Crawl4AI is not installed. |
| 3 | Challenge detection and handling | Done: Cloudflare, SiteGround sgcaptcha, reCAPTCHA, hCaptcha, DataDome, PerimeterX, Akamai, Incapsula, 403/429, JS challenge, access denied. Challenged domains are cooled down, not retried, queued for human verification, and the lead keeps its data. No bypass code. |
| 4 | Alternative sources with identity matching | Done for free sources: Internet Archive, YellowPages/Superpages cards, successor-site recovery, plus search/Places when keyed. Matches need name, phone, domain or address with zip. |
| 5 | Extraction quality and provenance | Done. Deterministic extraction with source, method, timestamp, confidence, verified/inferred per field. Company size is weak (0.8%). |
| 6 | Token and cost optimization | Partial. Boilerplate removal, content hash, URL/domain cache and cost accounting are done. No LLM runs on this path, so token savings are not measurable (0 tokens). |
| 7 | Dashboard | Done: API (`fetch_dashboard.py`) with date, provider, category and source filters, human-verification queue, and a portal page. The portal build was not verified here. |
| 8 | Testing and acceptance | Done at 266 sites per arm plus a 302-site audit, and a 20-lead pipeline run. The 80% target was not reached (see below). |

## 2. Measured results

Sample: Overture Maps places with websites, US cities and industries, three arms on disjoint slices of 266 sites.

| Metric | Before (legacy) | Fast path | Engine |
|---|---|---|---|
| Direct website fetch success | 63.5% | 65.8% | 65.4% (CI 59.5-70.9) |
| Acquisition, strict (fetch or new data recovered) | 63.5% | 65.8% | 69.9% |
| Acquisition incl. corroborated contact | 63.5% | 65.8% | 77.8% |
| Site email yield (the "~30%" figure) | 28.2% | 35.3% | 36.8% |
| Phone from site | 60.2% | 62.0% | 73.3% |
| Address from site | 40.2% | 36.5% | 42.1% |
| Owner / decision maker | 5.3% | 6.4% | 6.4% |
| Social links | 42.9% | 50.0% | 53.4% |
| Engine-only: description / services / technologies | n/a | n/a | 52.3% / 35.3% / 55.3% |
| Avg latency per site | 5.2 s | 5.2 s | 18.3 s |
| Provider cost, AI tokens | 0, 0 | 0, 0 | 0, 0 |

Alternative-source recovery (engine arm): 12 leads got a new email or phone (4.5%), 21 had an existing contact confirmed (7.9%), 54 stayed hint-only, 5 failed outright.

**80% was not reached.** Strict 69.9%, with corroboration 77.8%. The "~30% fetch success" premise did not hold: fetch success is about 65% on every arm, and the 30% matches email yield. The engine does not raise homepage fetch success measurably. Its gain is recovery, no wasted retries on challenged sites, and richer fields.

Why the rest fails (302-site audit): dead sites 16.6% (DNS 14.9%), bot protection 10.6% (SiteGround captcha, Cloudflare, plain 403), network errors 5.0%, JS shells 8.6%. Scrapling recovered 2%, Playwright 2.9% of what direct missed in this sample (24 of 97 in an earlier sample, mostly server disconnects).

Latency rose because recovery waits on public directories (3 s spacing per directory domain). `LEADX_ALT_DEADLINE_S` caps it.

## 3. System report

20-lead end-to-end run on the SQLite plane (Houston dentists, `benchmark_pipeline.py`): 160 discovered, 58 enriched and gated, 20 assigned, 20 delivered to the outbox, 0 failed, 18.5 s wall, first assigned lead at 6.7 s. 476 fetch attempts and 16 fetch outcomes were recorded. Dashboard over that run: 16 sites, 100% fetched, 73.2% average completeness, 2.3 s average fetch, 0 cost, 0 tokens.

| Area | Readiness |
|---|---|
| Discovery (Overture, OSM streaming) | Works at tested scale. Paid sources untested. |
| Enrichment / fetch engine | Works; fetch success is bounded by dead and blocking sites. |
| Verification | Syntax, DNS and MX only. SMTP checks need outbound port 25, which is blocked here, so deliverability is unverified. |
| Scoring, assignment, delivery | Pass on the SQLite plane. `client_projection_unavailable` appears in reconciliation because there is no Supabase client view. |
| Dashboard | API tested. Portal page not compiled or run in a browser. |

Test suite: 50 failures, identical to the 50 baseline ids (none new); `tests/test_fetch_engine.py` has 28 passing tests. `test_redis_runtime_policy` and `test_task7_isolated_api_worker_queue` are flaky under `-n 8` and pass alone.

Known risks:
* Sites remember repeated visits; SiteGround captcha rates climb on re-fetch. Cooldowns mitigate it.
* Directory recovery depends on YellowPages/Superpages markup and rate limits.
* Priors in `fetch_engine.History.PRIORS` come from one earlier sample.
* Three arms used different random slices, so differences of a few points are noise.

Needs to be verified with external resources:
* Provider keys: Firecrawl, Serper, Google Places, any LLM (token numbers).
* Port 25: SMTP mailbox verification.
* Postgres/Supabase: the fetch tables, RLS and the client projection. The SQL migration for the fetch tables has not been run against Postgres.
* Portal: `next build` and a visual check of the fetch dashboard page.

## 4. Recovery changes (js render, retry, crawl, index recovery)

- Discovery drops rows whose website host does not resolve (`free_discovery.drop_dead_domains`, counter `discovery_dead_domain`).
- Browser fetch waits for `load`, network idle (10 s), 400+ characters of visible text (6 s), then scrolls once. Browser timeout 45 s, Crawl4AI 50 s (`wait_until=load`, `page_timeout=45000`). Thin 200 pages and JS shells escalate to the browser.
- Network errors (timeout, disconnect, connect) are retried once with 1.75x timeout. Per-host concurrency 2. HTTP/2 is on when `h2` is installed (`LEADX_HTTP2=0` to compare).
- Crawl reads robots.txt and sitemap.xml (one index level) and fetches up to 12 pages ranked contact, about, team, service, location.
- Bot-protected sites: Common Crawl (CDX + WARC byte range), Wayback and search snippets, tagged `method=commoncrawl|wayback|search_snippet`. This is index recovery, reported apart from fetch success. Nothing here gets past a protection.
- Firecrawl runs last, behind key, policy, credit cap, `LEADX_FIRECRAWL_RUN_MAX` and a per-URL cache.

## 5. Recovery changes measured on the fixed 400-site list

Same list (`artifacts/fetch_audit/sites_fixed.json`: 400 sites, 354 whose domain resolves), same flags (engine arm, free path, no provider keys), same script. Numbers come from `artifacts/fetch_audit/before_fixed.json`, `before_fixed_t400.json` and `after_fixed.json`; the table is also in [FETCH_AUDIT.md](FETCH_AUDIT.md) section 4.

| Metric | Before (150 s bench wait) | Before (400 s bench wait) | After |
|---|---|---|---|
| Fetch success, all 400 sites | 68.2% | 68.2% | 72.5% |
| Fetch success, 354 DNS-live sites | 77.1% | 77.1% | 81.9% |
| Pages per fetched site | 4.66 | 4.68 | 6.47 |
| Core field completeness (email, phone, owner, social, address) | 42.7% | 43.5% | 42.9% |
| Email / phone / address / social from the site | 37.8 / 72.0 / 40.0 / 58.2% | 39.0 / 74.2 / 39.2 / 60.5% | 38.5 / 71.0 / 41.5 / 58.2% |
| Average latency per site | 15.8 s | 15.8 s | 21.7 s |
| Homepage recovered by browser or Crawl4AI | 8 + 1 | 4 + 0 | 22 + 2 |
| Homepage recovered by network retry | 6 | 8 | 6 |
| Index / alternative-source recovery (separate from fetch success) | 17 (4.2%) | 25 (6.2%) | 10 (2.5%) |
| Firecrawl calls | 0 | 0 | 0 |

Fairness check. The briefing assumed the bench's 150 s per-site wait truncated the old arm. The old arm has 18 `TimeoutError` failures on live sites at 150 s and still 13 at 400 s, with a wall time of 196 s, so those are not bench truncations. They are timeouts raised inside the old code path and counted as failures. After the changes there are 0. `scripts/fetch_audit.py` now reads the wait from `BENCH_SITE_TIMEOUT` (default 150). The after run (150 s) had no bench timeout, so it was not re-run at 400 s. The before arm was re-run on the old code (`d8170ad`) at 400 s: fetch success is identical, 68.2% and 77.1%.

Reading, without rounding up:
* Fetch success rose 4.3 points on all sites and 4.8 on live sites. Run-to-run noise is 1 to 2 points, so the gain is real but it is below the +5 to +7 expected for JS rendering plus +2 to +3 for retry, taken together or separately. Network retry added nothing measurable (6 recoveries before and after). The old arm's 13 to 18 internal `TimeoutError` failures account for most of the gap, so the browser path (22 recoveries against 4 to 8) is what turns them into fetches, not new reach into bot-protected sites.
* The remaining live-site failures after: Cloudflare 25, disconnect 8, 404 7, plain 403 5, TLS 6, other 4xx 3, parked 2, timeout 2, reCAPTCHA 2, one each access denied, 429, empty body, connect. Nothing in this change passes a protection.
* Sitemap and robots crawling raised pages per fetched site from 4.66 to 6.47 (+39%), but average core completeness did not move (42.7% to 42.9%). More pages did not add fields on this sample.
* Latency rose 37% (15.8 s to 21.7 s) because of the larger crawl, the longer browser wait and the retry.
* Index recovery fell from 17 or 25 leads to 10. Common Crawl was listed as a source for 101 leads (the count of leads that reached the alternative-source step; it only queries the index for bot-protected verdicts, so the number of real index queries is lower and was not recorded) and recovered none in this run, so the live path is unproven; Wayback gave 22 hits (51 before). The drop is mostly because sites that the old arm lost to its internal timeouts now fetch directly and need no recovery, and the Wayback count moves between runs. Do not read it as a Common Crawl gain.
* HTTP/2 A/B on the first 100 sites (after code): `LEADX_HTTP2=0` 62.0% / 70.5% live, 21.1 s per site; `LEADX_HTTP2=1` 61.0% / 69.3% live, 18.7 s per site, wall time 93.4 s against 93.1 s. No success difference beyond noise; latency per site is lower with HTTP/2 but wall time is equal.
* Discovery dead-domain drop: 46 of the 400 sites (11.5%) have no DNS record. `drop_dead_domains` removes such rows before enrichment, which is why the live-site column is the fair comparison for fetch success. The drop rate was measured on this stored list by the same DNS check, not by running a fresh discovery.

Not verified: real Firecrawl (0 calls, covered by mocked tests only), Serper/SearXNG, Google Places, SMTP deliverability (port 25 blocked), Supabase/Postgres, the portal page. One run per arm, so the sub-point differences above are not significant.
