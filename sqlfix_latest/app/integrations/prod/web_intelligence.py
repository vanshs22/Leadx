"""LeadX 14.0 Web Intelligence Mesh.

Turns the existing Scrapling/Crawl4AI/Firecrawl stack and public search into
independent research lanes. No private-account or cookie-based access is used.
"""
from __future__ import annotations
import asyncio, os, re
from typing import Any
from urllib.parse import urljoin
from app.integrations.prod.enricher import fetch_scrapling, fetch_crawl4ai, fetch_firecrawl, _parse_content
from app.integrations.prod.net_safety import assert_public_url
from app.integrations.prod.utils import extract_domain

FIRST_PARTY_PATHS=("/","/about","/about-us","/contact","/contact-us","/team","/our-team","/staff","/leadership","/services","/products","/pricing","/careers","/jobs","/locations","/blog","/news","/resources","/case-studies","/press","/sitemap.xml")
SOCIAL_DOMAINS=("linkedin.com","instagram.com","facebook.com","youtube.com","x.com","twitter.com","reddit.com")

def search_queries(company: str, domain: str, city: str = "") -> dict[str,list[str]]:
    c=company.strip(); d=domain.strip(); loc=f' "{city}"' if city else ""
    return {
      "linkedin":[f'site:linkedin.com/company "{c}"',f'site:linkedin.com/company "{d}"',f'site:linkedin.com/in "{c}" CEO',f'site:linkedin.com/in "{c}" founder',f'site:linkedin.com/in "{c}" owner'],
      "instagram":[f'site:instagram.com "{c}"{loc}',f'site:instagram.com "{c}" owner'],
      "facebook":[f'site:facebook.com "{c}"{loc}'],
      "youtube":[f'site:youtube.com "{c}"'],
      "x":[f'site:x.com "{c}"',f'site:twitter.com "{c}"'],
      "news":[f'"{c}" funding',f'"{c}" expansion',f'"{c}" hiring',f'"{c}" partnership',f'"{c}" launched'],
      "jobs":[f'"{c}" careers',f'"{c}" hiring'],
    }

async def crawl_company(website: str, *, use_crawl4ai: bool=True, use_firecrawl: bool=True, max_pages: int=12) -> dict[str,Any]:
    if not website: return {"ok":False,"error":"website_missing","facts":{},"evidence":[]}
    url=website if re.match(r"^https?://",website,re.I) else "https://"+website
    blocked=await assert_public_url(url)
    if blocked: return {"ok":False,"error":f"blocked:{blocked}","facts":{},"evidence":[]}
    domain=extract_domain(url); evidence=[]; contents=[]; queue=[url]; seen=set()
    while queue and len(seen)<max_pages:
        current=queue.pop(0).split("#",1)[0]
        if current in seen: continue
        seen.add(current)
        scrap=await fetch_scrapling(current)
        if scrap.get("ok"): contents.append((current, scrap.get("html") or scrap.get("text") or "","scrapling")); evidence.append({"source":"scrapling","url":current,"ok":True})
        if use_crawl4ai and len(seen)<=3:
            crawl=await fetch_crawl4ai(current)
            if crawl.get("ok"): contents.append((current,crawl.get("markdown") or "","crawl4ai")); evidence.append({"source":"crawl4ai","url":current,"ok":True})
        if use_firecrawl and len(seen)==1 and not scrap.get("ok"):
            fc=await fetch_firecrawl(current)
            if fc.get("ok"): contents.append((current,fc.get("markdown") or fc.get("html") or "","firecrawl")); evidence.append({"source":"firecrawl","url":current,"ok":True})
        html="\n".join(x[1] for x in contents[-2:])
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            if href.startswith("/") or href.startswith(url):
                absolute=urljoin(current,href)
                if extract_domain(absolute)==domain and any(k in absolute.lower() for k in ("contact","about","team","staff","leader","service","pricing","career","job","location","news","press","sitemap")):
                    queue.append(absolute)
    combined="\n".join(x[1] for x in contents)[:8_000_000]
    facts=_parse_content(combined) if combined else {}
    facts["website_domain"]=domain
    facts["web_paths_checked"]=[x[0] for x in contents]
    facts["web_crawlers_used"]=sorted({x[2] for x in contents})
    return {"ok":bool(combined),"facts":facts,"evidence":evidence,"negative_evidence":[],"domain":domain}
