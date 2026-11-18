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

## Added in the second pass

| Capability | Where | Why it beats the market |
|---|---|---|
| Saved smart searches with alerts | `POST /saved-searches`, `/saved-searches/{id}/run`, worker job `searches.alerts` (every 15 min), "Alert me about new matches" on the Leads page | Apollo-style alerts without credits; only leads delivered since the last run are reported |
| Signal plays | `lead.signal_detected` automation trigger, emitted by website change detection; conditions on `signal_type`/`strength`; actions `create_task`, `notify_owner`, `create_deal`... | HubSpot-style plays driven by free signals. Required fixing a defect: **every custom automation failed** on a foreign key (migration `20261117000400`) |
| Free website finder | `website_finder.py`; waterfall provider `website_guess`; runs first in enrichment for leads with no website | Probes likely domains and accepts one only if the page proves it is the same business (name, city, phone). Zero credits; Serper stays the fallback |
| LLM-assisted search parsing | `nl_search.parse_with_llm`, used only when the rules find nothing and an AI key exists | Output is validated against an allowlist before it reaches a query |
| Buying-committee map | `GET /companies/{id}/buying-committee` | Roles, coverage score, missing roles, who to contact first |
| Signed, testable webhooks | per-webhook secret (shown once), `x-leadx-signature-v2: t=,v1=` HMAC over `t.body`, `POST /webhooks/{id}/test`, `GET /webhooks/{id}/deliveries` | Replay-safe signatures; failed deliveries retry with backoff and stay visible |

## Not built yet

1. Playbook builder UI for plays (the backend trigger and actions exist; only the screen is missing).
2. A second free website source for businesses whose domain cannot be guessed (paid Serper remains the fallback).
3. Buying-committee and data-quality panels in the portal (APIs and client methods exist).
4. Realtime/RLS verification, mobile layouts, and the lead table's right-hand columns at 1360 px width (the "Open" button is clipped; horizontal scroll works).
5. Salesforce competitor research, AI-written outreach beyond the existing sequence writer (needs an AI key).

## Evidence

`tests/test_competitive_features.py` (10 offline checks), 74 live API checks in `scripts/local_stack/e2e.py`, and the real-browser suites
`browser_walk.mjs` (every page as client and admin) and `browser_journeys.mjs` (19 user journeys). See `E2E_TEST_REPORT.md`.
