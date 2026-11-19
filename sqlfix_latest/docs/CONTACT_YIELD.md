# Contact yield

Measured with `python scripts/benchmark_contact_yield.py 100 16` (site crawl through `enrich_one`, free path only, no provider keys, Firecrawl and Crawl4AI off, `LEADX_PAID_FALLBACK=never`). Same 100 sites, the first 100 of `artifacts/fetch_audit/sites_fixed.json` (a multi-city list, not only Houston), run once each. Before is commit `7c98042`, after is the FEAT-001/FEAT-002 head. No hints were passed, so this is what the site and the free recovery sources give without discovery data. Live web, so a rerun can differ by a few sites.

| Metric (of 100 sites) | Before | After |
|---|---|---|
| Sites with an email | 37 | 38 |
| Sites with a phone | 75 | 75 |
| Sites with both | 37 | 38 |
| Sites with neither | 25 | 25 |
| Enrichment returned an error (site unreachable or timeout, 60 s cap) | 24 | 24 |
| Sites keeping 2+ email candidates | 0 | 6 |
| Sites keeping 2+ phone candidates | 0 | 52 |

Primary email source: before 33 website, 4 archive; after 33 website, 5 archive.
Candidate counts after, by source: emails 41 website, 5 alt (alternative-source recovery); phones 187 website, 10 alt.
Phone primary source is not recorded in field provenance in either version, so no per-source split for the primary phone.

Reading it: the gain in the primary email is one site (archive recovery). The main change is that extra numbers and addresses are kept as candidates with provenance instead of being dropped. The 24 error sites are the same before and after, so the contact gap is mostly unreachable sites, not extraction.

## 20-lead pipeline run

`BENCH_DB=... python scripts/benchmark_pipeline.py "Houston, TX" dentist 20`, free path, after head:

- 160 discovered, 74 enriched and gated, 20 assigned and delivered, stop reason `target_met`, wall 37.1 s, shortfall 0.
- Of the 20 delivered contacts: 20 have a phone, 3 have an email, 3 have both.
- Verification observations (82): 74 phone `valid` (libphonenumber, format only), 4 email `syntax_mx_valid` (DNS only), 4 email SMTP probe `unknown` because outbound port 25 is blocked here.
- No email in this run is mailbox-verified. That needs port 25 egress or a validator; see `docs/DATA_ENGINE.md`.
