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
