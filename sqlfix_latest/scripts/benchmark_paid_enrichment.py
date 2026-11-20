"""Paid-fallback check on a few sites the free crawl left without an email. Spends at most N Firecrawl pages and N Serper searches.

usage: SERPER_API_KEY=... FIRECRAWL_API_KEY=... python scripts/benchmark_paid_enrichment.py "Houston, TX" dentist 10
"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LEADX_PAID_FALLBACK"] = "auto"
from app.integrations.prod.enricher import enrich_one  # noqa: E402
from app.integrations.prod.open_data import overture_places  # noqa: E402


async def main(loc, ind, n):
    rows = [r for r in await overture_places(ind, loc) if r.get("website")][:150]
    sem = asyncio.Semaphore(32)

    async def free(r):
        async with sem:
            try:
                return r, await asyncio.wait_for(enrich_one(r["website"], use_crawl4ai=False, use_firecrawl=False, company_name=r["name"]), 30)
            except Exception:
                return r, {}
    os.environ["LEADX_PAID_FALLBACK"] = "never"
    miss = [r for r, res in await asyncio.gather(*map(free, rows)) if not res.get("email")][:n]
    os.environ["LEADX_PAID_FALLBACK"] = "auto"
    t = time.monotonic()

    async def paid(r):
        s = time.monotonic()
        try:
            res = await asyncio.wait_for(enrich_one(r["website"], use_crawl4ai=False, use_firecrawl=True, company_name=r["name"], city="Houston"), 60)
        except Exception as e:
            res = {"error": type(e).__name__}
        return r["website"], res.get("email"), res.get("firecrawl_calls"), res.get("error"), round(time.monotonic() - s, 1)
    out = await asyncio.gather(*map(paid, miss))
    print("sites without free email:", len(miss), "| wall_s:", round(time.monotonic() - t, 1))
    for o in out:
        print(o)
    print("email found by paid fallbacks:", sum(1 for o in out if o[1]), "/", len(out), "| firecrawl pages:", sum(o[2] or 0 for o in out))

asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
