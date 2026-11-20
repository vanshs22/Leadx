"""Live extraction benchmark against an independent source.

Takes businesses from Overture Maps that list a website and a phone/email, fetches each website, runs the typed
extractor and measures agreement with Overture's listed values:

  phone_recall   share of businesses whose Overture phone appears among the phones extracted from the site
  email_recall   share of businesses whose Overture email appears among the emails extracted from the site
  email_precision share of extracted business-domain emails that are plausible (valid, not platform/placeholder)
  coverage_gain  share of sites where extraction found an email/phone that Overture did not list

Overture is a proxy, not ground truth: it can be stale, so recall below 100% is expected. Use it to compare releases
of the extractor against each other, not as an accuracy claim.

usage: python scripts/benchmark_extraction.py "Austin, TX" dentist 40
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.integrations.prod.enricher import _secondary_urls, fetch_scrapling  # noqa: E402
from app.integrations.prod.field_extract import extract_all  # noqa: E402
from app.integrations.prod.open_data import overture_places  # noqa: E402


def last10(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else d


async def one(row, sem):
    """Homepage plus up to 2 contact/about pages, the same shape of crawl the pipeline does."""
    async with sem:
        home = await fetch_scrapling(row["website"], timeout=20)
        if not home.get("html"):
            return None
        pages = [(home["html"], row["website"])]
        for url in _secondary_urls(row["website"], home["html"])[:2]:
            sub = await fetch_scrapling(url, timeout=20)
            if sub.get("html"):
                pages.append((sub["html"], url))
    phones, emails = set(), set()
    for html, url in pages:
        out = extract_all(html, url)
        phones |= {last10(p["value"]) for p in out.get("phones", [])}
        emails |= {e["value"].lower() for e in out.get("emails", [])}
    return {"name": row["name"], "ov_phone": last10(row.get("phone")), "ov_email": (row.get("email") or "").lower(),
            "phones": phones, "emails": emails, "pages": len(pages)}


async def main(location, industry, n):
    rows = [r for r in await overture_places(industry, location) if r.get("website") and (r.get("phone") or r.get("email"))][: n * 2]
    sem = asyncio.Semaphore(8)
    got = [g for g in await asyncio.gather(*[one(r, sem) for r in rows[:n * 2]]) if g][:n]
    ph = [g for g in got if g["ov_phone"]]
    em = [g for g in got if g["ov_email"]]
    ext_em = [e for g in got for e in g["emails"]]
    rep = {
        "sites_fetched": len(got),
        "phone_recall": round(sum(g["ov_phone"] in g["phones"] for g in ph) / len(ph), 3) if ph else None,
        "email_recall": round(sum(g["ov_email"] in g["emails"] for g in em) / len(em), 3) if em else None,
        "emails_extracted": len(ext_em),
        "coverage_gain_email": round(sum(bool(g["emails"]) and not g["ov_email"] for g in got) / len(got), 3) if got else None,
        "coverage_gain_phone": round(sum(bool(g["phones"] - {""}) and not g["ov_phone"] for g in got) / len(got), 3) if got else None,
        "extracted_emails_sample": ext_em[:10],
    }
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Austin, TX", sys.argv[2] if len(sys.argv) > 2 else "dentist", int(sys.argv[3]) if len(sys.argv) > 3 else 40))
