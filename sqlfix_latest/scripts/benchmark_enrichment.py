"""Crawl-only enrichment benchmark: how long does it take to enrich N real business websites, and how well?

Pulls N businesses with a website from Overture Maps, runs the production enrichment (enricher.enrich_one: fetch, contact
pages, typed extraction) with C in flight, and reports wall time, throughput, per-site latency and yield. Free path only:
Firecrawl and Serper are off.

usage: python scripts/benchmark_enrichment.py "Houston, TX" dentist 100 [concurrency=32]
"""
import asyncio
import collections
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LEADX_PAID_FALLBACK", "never")
from app.integrations.prod.enricher import enrich_one  # noqa: E402
from app.integrations.prod.open_data import overture_places  # noqa: E402


async def main(location: str, industry: str, n: int, conc: int):
    rows = [r for r in await overture_places(industry, location) if r.get("website")]
    seen, sites = set(), []
    for r in rows:                       # one site per domain: chains would otherwise be crawled many times
        d = r["website"].split("//")[-1].split("/")[0].removeprefix("www.")
        if d not in seen:
            seen.add(d)
            sites.append(r)
    sites = sites[:n]
    sem = asyncio.Semaphore(conc)
    lat, out = [], []

    async def one(r):
        async with sem:
            t = time.monotonic()
            try:
                res = await asyncio.wait_for(enrich_one(r["website"], use_crawl4ai=False, use_firecrawl=False, company_name=r["name"]),
                                             timeout=float(os.getenv("BENCH_TIMEOUT", "45")))
                err = res.get("error")
            except asyncio.TimeoutError:
                res, err = {}, "timeout"
            except Exception as exc:
                res, err = {}, type(exc).__name__
            lat.append(time.monotonic() - t)
            out.append((r, res, err))

    t0 = time.monotonic(); c0 = time.process_time()
    await asyncio.gather(*[one(r) for r in sites])
    wall = time.monotonic() - t0
    ok = [x for x in out if x[1].get("enrichment_ok")]
    with_email = [x for x in out if x[1].get("email")]
    with_phone = [x for x in out if x[1].get("phone_from_web")]
    errors = collections.Counter(str(x[2])[:40] for x in out if x[2])
    lat.sort()
    print(json.dumps({
        "sites": len(sites), "concurrency": conc, "wall_s": round(wall, 1), "cpu_s": round(time.process_time() - c0, 1), "sites_per_s": round(len(sites) / wall, 2),
        "latency_p50_s": round(statistics.median(lat), 1), "latency_p90_s": round(lat[int(0.9 * (len(lat) - 1))], 1), "latency_max_s": round(lat[-1], 1),
        "enriched_ok_pct": round(100 * len(ok) / len(sites)), "email_pct": round(100 * len(with_email) / len(sites)), "phone_pct": round(100 * len(with_phone) / len(sites)),
        "errors": dict(errors.most_common(6)),
    }, indent=1))


if __name__ == "__main__":
    a = sys.argv
    asyncio.run(main(a[1] if len(a) > 1 else "Houston, TX", a[2] if len(a) > 2 else "dentist", int(a[3]) if len(a) > 3 else 100, int(a[4]) if len(a) > 4 else 32))
