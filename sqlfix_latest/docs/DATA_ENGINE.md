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
| Directories | `directory_discovery.py`, `directory_extract.py` | per country | Listing pages are paged until a page adds nothing new. YellowPages, Superpages, Hotfrog parse fully; BBB lists phone/address (profile pages return 403). |
| Web search | `web_search.py` | any | SearXNG first, every page until no new URL; Serper only if SearXNG returns nothing. Also feeds social profile search and per-lead contact lookup. |
| OSM | `exhaustive_discovery.py` | global | Overpass, falling back to Nominatim. |

Directory controls: `LEADX_DIRECTORY_SCRAPING=0` kill switch, robots.txt respected by default (`LEADX_DIRECTORY_ROBOTS`, per-domain override in `LEADX_DIRECTORY_POLICY`), per-domain spacing, circuit breaker after 3 block responses, no CAPTCHA solving. Domains that block us (manta, chamberofcommerce, cylex, brownbook, yelp returned 403 or disallow crawling from this sandbox) are skipped, not bypassed.

`bing_search` was removed (the Bing Search API is retired).

## Extraction (`field_extract.py`)

Typed extractors for email, phone, social, link hubs, people, address and JSON-LD business data, each with a validator and the page it came from. Rejects platform/placeholder domains, image filenames, no-reply addresses and unknown TLDs; decodes Cloudflare-protected and `[at]` addresses. Persistence normalises with `utils.normalize_email`, and the database refuses malformed emails (`*_email_format_chk`, migration 47).

Quality check: `tests/test_extraction_golden.py` runs 100 synthetic pages (precision/recall floors). The pages were written by the same author as the extractor, so this guards regressions; it does not measure real-world accuracy. `scripts/benchmark_extraction.py` compares site extraction with Overture values for real businesses (see results below).

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

Not verified: the real project's schema is behind this repository (no `tenants.settings`, no migration 48 tables), so the 10-lead run and the new engine tables on Supabase wait for the schema reset. HubSpot and Salesforce delivery against developer accounts (no credentials). Companies House live. Email deliverability beyond MX (port 25 is blocked, no paid verifier key). Phone liveness (no provider key). Serper (account has no credits).

Extraction benchmark (Austin dentists, 40 sites, homepage plus two contact pages): the site lists the same phone as Overture for 70% of businesses and the same email for 36%. The misses inspected were sites listing a different number (other location, tracking number) or JavaScript-only pages, so this is a disagreement rate against a source that can itself be stale, not an accuracy figure.
