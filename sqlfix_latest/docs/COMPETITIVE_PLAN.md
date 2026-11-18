# Competitive plan: what the market does, what LeadX had, what changed

Sources read on 2026-11-18 (vendor pages and third-party reviews, paraphrased): Clay docs on waterfalls and Claygents
([waterfall](https://agent-docs.clay.com/faq/enrichment-cheaper-provider-first-fallback-waterfall),
[platform](https://agent-docs.clay.com/faq/one-platform-for-enrichment-research-and-outbound-triggers)), Apollo's pages on
[saved searches](https://www.apollo.io/insights/which-software-offers-the-best-saved-search-features) and
[AI assistants](https://www.apollo.io/insights/how-do-ai-sales-assistants-personalize-outreach-at-scale), HubSpot's
[prospecting agent](https://knowledge.hubspot.com/prospecting/use-the-prospecting-agent), Instantly's
[enrichment guide](https://instantly.ai/blog/lead-enrichment/). Salesforce was **not** researched in this pass.

## What each competitor leads with

| Product | Strength we should match | Where it is weak for our users |
|---|---|---|
| Clay | Cheapest-first provider waterfall with validation; 150+ providers; AI research agents; signal triggers | Credit pricing; no CRM or pipeline of its own |
| Apollo | One prompt to list to sequence; saved searches that alert and trigger sequences; intent topics; large contact database | Data is a shared database, so leads are not exclusive |
| HubSpot | Signal-driven prospecting "plays"; fit + engagement scoring; CRM data feeds AI | Credits per AI action; weak lead discovery |
| Instantly | Lead finder, enrichment and deliverability tooling in one place | Thin CRM |

## LeadX position (verified in code, not marketing)

| Capability | Before this pass | Now | Differentiator vs competitors |
|---|---|---|---|
| Waterfall enrichment, cheapest first | Existed (`waterfall.py`, provider registry) | Unchanged | Free lanes (Scrapling, Crawl4AI, OSM, local SMTP/DNS) run before any paid key; paid providers are listed as fallbacks only |
| Technographics, signals, why-now | Existed (`signals.py`) | Unchanged | |
| **Plain-English lead search** | Only a keyword box and an agent planner | `POST /crm/{t}/leads/nl-search` + "Smart search" toggle on the Leads page | Deterministic and free (no LLM credits); shows exactly how each phrase was read |
| **Data completeness / confidence + recovery plan** | Only an agent summary | `GET /leads/{id}/data-quality`, `GET /data-quality/summary` | Each gap lists providers that can fill it, free first, with cost |
| **Duplicate detection and merge** | None | `GET /duplicates`, `POST /companies/merge`, `/contacts/merge` + Duplicates page | Merge re-points deals, notes, tasks, activities; audited; tenant-safe |
| **Website change detection** | Re-verify timers only | `POST /leads/{id}/refresh-check`, `GET /leads/{id}/changes` | Detects redesign, new/removed contact details and tech changes from free fetches; writes `lead_signals` (buying-signal feed). Paid Firecrawl only if the caller opts in |
| Exclusive leads, agency workspaces, white-label | Existed | Unchanged | Competitors sell shared data |
| Cursor pagination on the lead list | Existed, but cursor pages were served from the first page's cache | Fixed | |

Also fixed in this pass: lead list filters (`city`, `state`, `min_score`, `min_rating`, `has_email/phone/website/booking`,
`delivered_since_days`) so every saved view and the new search use real database filters.

## Not built yet (ordered by value for effort)

1. **Saved smart searches with alerts** (Apollo's strongest 2026 theme): store the parsed filters as a view, run on the schedule worker, notify on new matches.
2. **Signal "plays"** (HubSpot): a play = filters + signal + action; the automation engine and `lead_signals` already exist, only the builder UI and trigger wiring are missing.
3. **Free website finder for leads that have only a name and city** (the main reason OSM-only runs fall short of target). Needs a legal free source; a paid search key (Serper) remains the fallback.
4. **LLM-assisted parsing** for queries the rules cannot read, behind the existing AI key check.
5. **Account-level buying committee map** from the contact roles table.
6. Per-tenant RLS and Realtime verification, theme redesign, and browser tests (see `E2E_TEST_REPORT.md`).

## Evidence

`tests/test_competitive_features.py` (6 offline checks) and 9 new live checks in `scripts/local_stack/e2e.py`
(search, tenant isolation, injection attempt, data quality, change detection against a local test page, merge re-pointing,
cross-tenant merge refusal). Run results: see `E2E_TEST_REPORT.md`.
