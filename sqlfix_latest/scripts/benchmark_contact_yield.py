"""Contact yield on a fixed site list: share of sites with an email, a phone, both, and which source supplied each. Free path only.

usage: python scripts/benchmark_contact_yield.py [n=100] [concurrency=16] > out.json
Runs against whatever checkout it is in, so the same file measures the baseline worktree and HEAD.
"""
import asyncio
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LEADX_PAID_FALLBACK"] = "never"
from app.integrations.prod.enricher import enrich_one  # noqa: E402

SITES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "fetch_audit", "sites_fixed.json")


async def main(n: int, conc: int):
    sites = json.load(open(SITES))[:n]
    sem = asyncio.Semaphore(conc)
    out = []

    async def one(r):
        async with sem:
            try:
                res = await asyncio.wait_for(enrich_one(r["website"], use_crawl4ai=False, use_firecrawl=False, company_name=r["name"], city=r.get("city")), 60)
            except Exception as exc:
                res = {"error": type(exc).__name__}
            out.append(res)

    await asyncio.gather(*[one(r) for r in sites])
    stat = collections.Counter()
    src = {k: collections.Counter() for k in ("email", "phone", "email_cand", "phone_cand")}
    for res in out:
        prov = res.get("field_provenance") or {}
        e = bool(res.get("email") or res.get("email_candidates")); p = bool(res.get("phone_from_web") or res.get("phone_candidates"))
        stat["email"] += e; stat["phone"] += p; stat["both"] += e and p; stat["neither"] += not (e or p)
        stat["email_candidates_2plus"] += len(res.get("email_candidates") or []) > 1
        stat["phone_candidates_2plus"] += len(res.get("phone_candidates") or []) > 1
        for k, f in (("email", "email"), ("phone", "phone_from_web")):
            s = (prov.get(f) or {}).get("source") or (prov.get(f) or {}).get("method")
            if s:
                src[k][str(s)] += 1
        for k in ("email", "phone"):
            for c in res.get(k + "_candidates") or []:
                src[k + "_cand"][str(c.get("source", "?")).split(":")[0]] += 1
    print(json.dumps({"sites": len(sites), **{k: v for k, v in stat.items()}, "primary_source": {k: dict(v) for k, v in src.items()},
                      "errors": sum(1 for r in out if r.get("error"))}, indent=1))


if __name__ == "__main__":
    a = sys.argv
    asyncio.run(main(int(a[1]) if len(a) > 1 else 100, int(a[2]) if len(a) > 2 else 16))
