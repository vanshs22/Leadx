"""Website acquisition engine: cheapest suitable fetch method first, history-aware escalation, no bot-protection bypass.

acquire(url) runs the direct pooled HTTP fetch and classifies the answer (fetch_classify). Only a verdict that a different
method can plausibly fix escalates, and the next provider is chosen by expected success per cost from observed history
(per domain, per protection vendor, per provider), not by walking a fixed list.

  usable page              -> done
  JS shell / redirect stub -> local browser (Playwright, or Crawl4AI when installed). Firecrawl is not part of the cascade: see firecrawl_last_resort
  network error            -> Scrapling (different HTTP stack), scheme/www flip
  challenge, 403, 429      -> STOP. Classified, cooled down per domain, reported to the caller, never retried through another fetcher.
  dead domain / 404 site   -> STOP.

Controls: per-provider concurrency limits, per-provider timeouts, per-provider circuit breakers (open on provider faults, not on
site refusals), per-host politeness limit, domain cooldowns, URL result cache. Every attempt is sent to fetch_telemetry.

Env: LEADX_FETCH_ENGINE=0 disables the engine (callers fall back to the plain direct-then-Scrapling path);
LEADX_FETCH_PROVIDERS (comma list, default direct,scrapling,browser,crawl4ai,firecrawl); LEADX_FETCH_BROWSER_CONCURRENCY (4);
LEADX_FETCH_HOST_CONCURRENCY (2); LEADX_FETCH_NET_RETRY (1: retry timeout/disconnect/connect once with a 1.75x timeout); LEADX_HTTP2 (1); LEADX_FETCH_CACHE_SECONDS (300); LEADX_FETCH_COOLDOWN_SCALE (1.0); LEADX_FETCH_BUDGET_S (60, total time per URL across providers).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.integrations.prod.fetch_classify import Verdict, classify_error, classify_response, visible_text_len
from app.integrations.prod.net_safety import assert_public_url, safe_get

logger = logging.getLogger("lead_engine.fetch_engine")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
HEADERS = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
# relative cost of one attempt (credits for paid, a small CPU/latency weight for local) and per-attempt timeout
PROVIDERS = {
    "direct": {"cost": 0.0, "weight": 0.2, "timeout": 20},
    "scrapling": {"cost": 0.0, "weight": 0.5, "timeout": 20},
    "browser": {"cost": 0.0, "weight": 3.0, "timeout": 45},
    "crawl4ai": {"cost": 0.0, "weight": 3.0, "timeout": 50},
    "firecrawl": {"cost": float(os.getenv("FIRECRAWL_CREDITS_PER_PAGE", "1")), "weight": 5.0, "timeout": 60},
}
# which providers a verdict category may escalate to (challenges and refusals escalate nowhere, by design)
ESCALATE = {
    "js_shell": ["browser", "crawl4ai"], "redirect_stub": ["browser", "crawl4ai"], "empty_body": ["browser", "scrapling", "crawl4ai"],
    "disconnect": ["browser", "scrapling"], "connect": ["scrapling", "browser"], "tls": ["scrapling", "browser"], "network_other": ["scrapling", "browser"],
    "timeout": [], "ok": ["browser"], "http_5xx": ["browser", "scrapling"], "http_4xx": [],
}


_NET_RETRY = ("timeout", "disconnect", "connect", "network_other")


def _enabled_providers() -> list[str]:
    raw = os.getenv("LEADX_FETCH_PROVIDERS", "direct,scrapling,browser,crawl4ai,firecrawl")
    return [p.strip() for p in raw.split(",") if p.strip() in PROVIDERS]


def enabled() -> bool:
    return os.getenv("LEADX_FETCH_ENGINE", "1") != "0"


# hosting platforms where every subdomain is a different business: cooldowns and history must not spread across them
PLATFORMS = {"glossgenius.com", "square.site", "godaddysites.com", "wixsite.com", "weebly.com", "business.site", "myshopify.com", "wordpress.com", "blogspot.com", "site123.me",
             "mystrikingly.com", "carrd.co", "webflow.io", "vagaro.com", "booksy.com", "hub.biz", "trustab.org", "placeweb.site", "jany.io", "klo7.io", "squarespace.com", "sites.google.com",
             "mindbodyonline.com", "setmore.com", "acuityscheduling.com", "fresha.com", "wixstudio.io", "netlify.app", "vercel.app", "github.io", "pages.dev"}


def registered_domain(url_or_host: str) -> str:
    h = (urlparse(url_or_host).hostname if "//" in url_or_host else url_or_host) or ""
    h = h.lower().removeprefix("www.")
    extra = {x.strip() for x in os.getenv("LEADX_FETCH_PLATFORM_DOMAINS", "").split(",") if x.strip()}
    if any(h == p or h.endswith("." + p) for p in PLATFORMS | extra):
        return h
    parts = h.split(".")
    if len(parts) >= 3 and (parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


@dataclass
class Breaker:
    threshold: int = 5
    open_for: float = 60.0
    fails: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.fails < self.threshold:
            return True
        return time.monotonic() - self.opened_at >= self.open_for  # half-open: one probe through after the pause

    def record(self, provider_ok: bool) -> None:
        if provider_ok:
            self.fails = 0
        else:
            self.fails += 1
            if self.fails >= self.threshold:
                self.opened_at = time.monotonic()


class History:
    """Observed outcomes: (provider) overall, (provider, domain), (provider, protection vendor)."""

    # starting beliefs from an earlier 450-site audit run (see docs/FETCH_AUDIT.md, provider table): (provider, direct verdict) -> [tries, successes]; real history replaces them
    PRIORS = {("browser", "disconnect"): [19, 16], ("scrapling", "disconnect"): [19, 1], ("browser", "http_5xx"): [9, 7], ("scrapling", "http_5xx"): [9, 0],
              ("browser", "empty_body"): [1, 1], ("scrapling", "empty_body"): [1, 1], ("scrapling", "tls"): [3, 0], ("scrapling", "connect"): [1, 0]}

    def __init__(self) -> None:
        self.prov: dict[str, list] = {}      # provider -> [n, ok, ms_total]
        self.dom: dict[tuple, list] = {}      # (provider, domain) -> [n, ok]
        self.vend: dict[tuple, list] = {k: list(v) for k, v in self.PRIORS.items()}     # (provider, trigger category) -> [n, ok]

    def add(self, provider: str, domain: str, category_in: str, ok: bool, ms: float) -> None:
        p = self.prov.setdefault(provider, [0, 0, 0.0]); p[0] += 1; p[1] += int(ok); p[2] += ms
        d = self.dom.setdefault((provider, domain), [0, 0]); d[0] += 1; d[1] += int(ok)
        v = self.vend.setdefault((provider, category_in), [0, 0]); v[0] += 1; v[1] += int(ok)

    def p_success(self, provider: str, domain: str, category_in: str) -> float:
        """Smoothed success probability: provider+situation history, overridden by what this domain has already shown."""
        v = self.vend.get((provider, category_in), [0, 0]); d = self.dom.get((provider, domain), [0, 0])
        if d[0] >= 2 and d[1] == 0:
            return 0.0
        base = (v[1] + 0.5) / (v[0] + 1.0)
        return base if d[0] == 0 else (d[1] + base) / (d[0] + 1.0)


@dataclass
class Domain:
    cooldown_until: float = 0.0
    category: str = ""
    kind: str = ""
    vendor: str = ""
    since: float = 0.0


class Engine:
    def __init__(self) -> None:
        self.history = History()
        self.breakers: dict[str, Breaker] = {p: Breaker() for p in PROVIDERS}
        self.domains: dict[str, Domain] = {}
        self.cache: dict[str, tuple[float, dict]] = {}
        self._sems: dict[tuple, asyncio.Semaphore] = {}
        self._browser = None
        self._browser_lock: Optional[asyncio.Lock] = None
        self._loop = None

    # ---- concurrency -------------------------------------------------------------------------------------------------
    def _sem(self, key: tuple, n: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:   # semaphores and the browser belong to one event loop
            self._sems.clear(); self._browser = None; self._browser_lock = None; self._loop = loop
        s = self._sems.get(key)
        if s is None:
            s = self._sems[key] = asyncio.Semaphore(n)
        return s

    def _limit(self, provider: str) -> int:
        return {"browser": int(os.getenv("LEADX_FETCH_BROWSER_CONCURRENCY", "4")), "crawl4ai": 2, "firecrawl": 4, "scrapling": 16}.get(provider, 256)

    # ---- domain cooldowns --------------------------------------------------------------------------------------------
    def cooling(self, url: str) -> Optional[Domain]:
        d = self.domains.get(registered_domain(url))
        return d if d and d.cooldown_until > time.time() else None

    def mark(self, url: str, v: Verdict) -> None:
        if not v.usable and (v.kind in ("challenge", "rate_limit", "parked") or v.category == "dns" or (v.category == "http_404" and urlparse(url).path in ("", "/"))):
            scale = float(os.getenv("LEADX_FETCH_COOLDOWN_SCALE", "1.0"))
            self.domains[registered_domain(url)] = Domain(time.time() + v.cooldown_s * scale, v.category, v.kind, v.category, time.time())

    def clear_cooldown(self, host: str) -> bool:
        return self.domains.pop(registered_domain(host), None) is not None

    # ---- providers ---------------------------------------------------------------------------------------------------
    async def _direct(self, url: str, timeout: float) -> dict:
        r, err = await safe_get(url, timeout=timeout, headers=HEADERS)
        if r is None:
            return {"status": None, "html": "", "error": err, "headers": {}}
        return {"status": r.status_code, "html": r.text or "", "error": None, "headers": dict(r.headers), "final_url": str(r.url)}

    async def _scrapling(self, url: str, timeout: float) -> dict:
        try:
            from scrapling.fetchers import AsyncFetcher
        except ImportError:
            from scrapling import AsyncFetcher
        fetcher = AsyncFetcher(auto_match=False)
        current = url
        page = None
        for _ in range(6):
            page = await fetcher.get(current, timeout=timeout, follow_redirects=False, stealthy_headers=False, headers=HEADERS)
            loc = (getattr(page, "headers", None) or {}).get("location")
            if getattr(page, "status", None) in (301, 302, 303, 307, 308) and loc:
                current = urljoin(current, str(loc))
                blocked = await assert_public_url(current)
                if blocked:
                    return {"status": None, "html": "", "error": f"blocked_redirect:{blocked}", "headers": {}}
                continue
            break
        html = getattr(page, "html", "") or getattr(page, "body", "") or ""
        html = html.decode("utf-8", "ignore") if isinstance(html, (bytes, bytearray)) else str(html)
        return {"status": getattr(page, "status", None), "html": html[:2_500_000], "error": None, "headers": dict(getattr(page, "headers", None) or {})}

    async def _get_browser(self):
        if self._browser_lock is None:
            self._browser_lock = asyncio.Lock()
        async with self._browser_lock:
            if self._browser is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
            return self._browser

    async def close(self) -> None:
        b, self._browser = self._browser, None
        if b:
            try:
                await b.close(); await self._pw.stop()
            except Exception as exc:
                logger.debug("browser close: %s", type(exc).__name__)

    async def _browser_fetch(self, url: str, timeout: float) -> dict:
        """Local headless Chromium with its own honest user agent. Images/media/fonts are not loaded, and every request it makes
        (including sub-requests and redirects) is checked as a public URL, same as the HTTP path.
        shortcut: Chromium re-resolves DNS after our check (rebinding window), same limit as any pre-resolved check; upgrade by running the browser in a network namespace without private routes."""
        browser = await self._get_browser()
        ctx = await browser.new_context(java_script_enabled=True, ignore_https_errors=False, service_workers="block")
        try:  # route() does not see WebSockets; a hostile page must not be able to open one to an internal host
            await ctx.route_web_socket("**/*", lambda ws: asyncio.ensure_future(ws.close()))
        except AttributeError:
            logger.debug("playwright without route_web_socket")
        seen: dict[str, bool] = {}

        async def gate(route):
            req = route.request
            if req.resource_type in ("image", "media", "font"):
                return await route.abort()
            host = urlparse(req.url).hostname or ""
            if host not in seen:
                seen[host] = not await assert_public_url(req.url)
            await (route.continue_() if seen[host] else route.abort())

        await ctx.route("**/*", gate)
        try:
            page = await ctx.new_page()
            # the waits after goto add up to ~17 s; keep the whole fetch under `timeout` so run_provider's deadline does not discard a rendered page
            resp = await page.goto(url, wait_until="load", timeout=max(timeout - 17, 10) * 1000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as exc:  # pages with long-polling never go idle; the DOM is already there
                logger.debug("networkidle not reached: %s", type(exc).__name__)
            try:  # client-side apps paint after load: wait for real visible text
                await page.wait_for_function("() => ((document.body && document.body.innerText) || '').trim().length > 400", timeout=6000)
            except Exception as exc:
                logger.debug("body text stayed short: %s", type(exc).__name__)
            try:  # one scroll to trigger lazy-loaded sections
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(500)
            except Exception as exc:
                logger.debug("scroll failed: %s", type(exc).__name__)
            html = await page.content()
            return {"status": resp.status if resp else None, "html": html[:2_500_000], "error": None, "headers": dict(resp.headers) if resp else {}, "final_url": page.url}
        finally:
            await ctx.close()

    async def _crawl4ai(self, url: str, timeout: float) -> dict:
        from app.integrations.prod.enricher import fetch_crawl4ai
        r = await fetch_crawl4ai(url)
        return {"status": 200 if r.get("ok") else None, "html": "\n".join(x for x in (r.get("html"), r.get("markdown")) if x), "error": r.get("error"), "headers": {}}

    async def _firecrawl(self, url: str, timeout: float) -> dict:
        from app.integrations.prod.enricher import fetch_firecrawl
        r = await fetch_firecrawl(url)
        return {"status": 200 if r.get("ok") else None, "html": "\n".join(x for x in (r.get("html"), r.get("markdown")) if x), "error": r.get("error"), "headers": {}}

    def _available(self, provider: str) -> bool:
        if provider not in _enabled_providers() or not self.breakers[provider].allow():
            return False
        if provider == "browser":
            try:
                import playwright  # noqa: F401
            except ImportError:
                return False
            return os.getenv("LEADX_FETCH_BROWSER", "1") != "0"
        if provider == "crawl4ai":
            try:
                from app.integrations.prod.enricher import ensure_crawl4ai_dir
                ensure_crawl4ai_dir()
                import crawl4ai  # noqa: F401
            except ImportError:
                return False
        if provider == "firecrawl":
            from app.integrations.prod.paid_policy import local_mode_denies_paid, paid_fallback_mode
            return bool(os.getenv("FIRECRAWL_API_KEY") or os.getenv("FIRECRAWL_API_KEYS")) and paid_fallback_mode() != "never" and not local_mode_denies_paid()
        return True

    async def run_provider(self, provider: str, url: str, category_in: str = "first", timeout: Optional[float] = None) -> tuple[dict, Verdict, dict]:
        """One attempt: bounded by the provider's concurrency limit, timeout and breaker. Returns (raw, verdict, attempt record)."""
        cfg = PROVIDERS[provider]
        tmo = timeout or cfg["timeout"]
        fn = getattr(self, "_" + ("browser_fetch" if provider == "browser" else provider))
        t0 = time.monotonic()
        provider_fault = False
        async with self._sem((provider,), self._limit(provider)):
            try:
                raw = await asyncio.wait_for(fn(url, tmo), timeout=tmo + 5)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                raw = {"status": None, "html": "", "error": "TimeoutError: provider deadline", "headers": {}}
            except Exception as exc:
                raw = {"status": None, "html": "", "error": f"{type(exc).__name__}: {exc}"[:300], "headers": {}}
                provider_fault = provider != "direct" and not _site_side(str(exc))
        ms = (time.monotonic() - t0) * 1000
        blocked = str(raw.get("error") or "").startswith("blocked")
        v = classify_error(raw.get("error")) if raw.get("status") is None else classify_response(raw["status"], raw["html"], raw.get("headers"))
        if provider != "direct" and v.usable is False and raw.get("status") is None and not provider_fault and not blocked:
            provider_fault = "playwright" in str(raw.get("error")).lower() or "executable" in str(raw.get("error")).lower() or "firecrawl" in str(raw.get("error")).lower()
        self.breakers[provider].record(not provider_fault)
        dom = registered_domain(url)
        self.history.add(provider, dom, category_in, v.usable, ms)
        att = {"provider": provider, "category": v.category, "kind": v.kind, "status": raw.get("status"), "ok": v.usable, "ms": round(ms),
               "bytes": len(raw.get("html") or ""), "cost": cfg["cost"] if provider == "firecrawl" and raw.get("status") else 0.0,
               "error": (raw.get("error") or "")[:160] or None, "trigger": category_in, "domain": dom, "url": url[:300]}
        return raw, v, att

    # ---- the cascade -------------------------------------------------------------------------------------------------
    def _next(self, v: Verdict, tried: set, domain: str) -> Optional[str]:
        cands = [p for p in ESCALATE.get(v.category, []) if p not in tried and self._available(p)]
        if not cands:
            return None
        def score(p):
            ps = self.history.p_success(p, domain, v.category)
            return ps / (0.1 + PROVIDERS[p]["cost"] + PROVIDERS[p]["weight"] * 0.2)
        best = max(cands, key=score)
        return best if self.history.p_success(best, domain, v.category) > 0.02 else None

    async def _direct_with_fixes(self, url: str, timeout: float) -> tuple[dict, Verdict, list[dict]]:
        """Direct fetch plus the free, non-evasive fixes: same-site JS/meta redirect stub, dead path -> homepage, scheme/www flip, one short 429 wait."""
        atts: list[dict] = []
        raw, v, a = await self.run_provider("direct", url, "first"); atts.append(a)
        if v.category in _NET_RETRY and os.getenv("LEADX_FETCH_NET_RETRY", "1") != "0":
            await asyncio.sleep(random.uniform(0.5, 1.5))   # a dropped connection often succeeds on a calmer second try with more time
            raw, v, a = await self.run_provider("direct", url, v.category, timeout=PROVIDERS["direct"]["timeout"] * 1.75); atts.append(a)
        p = urlparse(url)
        alt = None
        if v.category == "redirect_stub":
            m = re.search(r"""(?:location(?:\.href)?\s*=\s*|content=["']\d+;\s*(?:url=)?)["']?([^"'\s>;]+)""", raw["html"], re.I)
            if m and "captcha" not in m.group(1).lower():
                alt = urljoin(url, m.group(1))
                if registered_domain(alt) != registered_domain(url):
                    alt = None
        elif v.category == "http_404" and (p.path.strip("/") or p.query):
            alt = f"{p.scheme}://{p.netloc}/"
        elif v.category in ("dns", "connect", "tls", "disconnect", "network_other"):
            host = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
            alt = f"{'http' if p.scheme == 'https' else 'https'}://{host}{p.path or '/'}"
        elif v.category == "http_429" and _retry_after(raw.get("headers") or {}) <= 5:
            await asyncio.sleep(2.0); alt = url
        if alt and not await assert_public_url(alt):
            raw2, v2, a2 = await self.run_provider("direct", alt, v.category); atts.append(a2)
            if v2.usable or (v2.kind == v.kind and v2.category != v.category and v.category in ("dns", "connect", "tls", "network_other")):
                raw, v = raw2, v2
        return raw, v, atts

    async def acquire(self, url: str, *, light: bool = False, timeout: float = 20) -> dict:
        """Fetch one URL. Returns the legacy fetch dict (html, text, ok, status, error, method) plus category/kind/attempts/cost.
        ``light`` (secondary pages) uses the direct provider only and never escalates."""
        from app.integrations.prod import fetch_telemetry as tel
        t0 = time.monotonic()
        out = {"html": "", "text": "", "method": "direct", "ok": False, "error": None, "status": None, "category": None, "kind": None,
               "attempts": [], "cost": 0.0, "cached": False, "cooldown": False}
        blocked = await assert_public_url(url)
        if blocked:
            v = classify_error(f"blocked:{blocked}")  # an unresolvable name is a DNS failure, a private address is an SSRF refusal
            out.update(error=f"blocked:{blocked}", category=v.category, kind=v.kind)
            self.mark(url, v)
            return out
        ttl = float(os.getenv("LEADX_FETCH_CACHE_SECONDS", "300"))
        hit = self.cache.get(url)
        if hit and time.time() - hit[0] < ttl:
            return {**hit[1], "cached": True, "attempts": [], "cost": 0.0}   # a cache hit spends nothing
        cool = self.cooling(url)
        if cool:
            out.update(error=f"cooldown:{cool.category}", category=cool.category, kind=cool.kind, cooldown=True)
            return out
        host = registered_domain(url)
        async with self._sem(("host", host), int(os.getenv("LEADX_FETCH_HOST_CONCURRENCY", "2"))):
            raw, v, atts = await self._direct_with_fixes(url, timeout)
            tried = {"direct"}
            budget = float(os.getenv("LEADX_FETCH_BUDGET_S", "60"))  # total wall time one URL may spend across providers
            while not light and time.monotonic() - t0 < budget and (not v.usable or _thin(v, raw["html"])):
                nxt = self._next(v, tried, host)
                if not nxt:
                    break
                tried.add(nxt)
                raw2, v2, a2 = await self.run_provider(nxt, url, v.category); atts.append(a2)
                if v2.usable and visible_text_len(raw2["html"]) > visible_text_len(raw["html"]):
                    raw, v = raw2, v2
                    out["method"] = nxt
                elif v2.is_challenge or v2.terminal:
                    v = v2 if not v.usable else v
                    break
        out.update(html=(raw["html"] if v.usable else ""), ok=v.usable, status=raw.get("status"), category=v.category, kind=v.kind,
                   error=None if v.usable else (raw.get("error") or v.category), attempts=atts, cost=round(sum(a["cost"] for a in atts), 4),
                   elapsed_ms=round((time.monotonic() - t0) * 1000), final_url=raw.get("final_url"))
        out["text"] = out["html"]
        out["challenge"] = v.category if v.is_challenge else None
        if v.usable:
            self.cache[url] = (time.time(), {k: val for k, val in out.items() if k != "attempts"})
            if len(self.cache) > 1000:
                self.cache.pop(next(iter(self.cache)))
        else:
            self.mark(url, v)
        tel.record_attempts(atts)
        return out


_FC_CACHE: dict[str, dict] = {}      # url -> result, no TTL: a URL is never paid for twice in this process
_FC_CALLS: dict[str, int] = {}       # run id -> paid calls made


async def firecrawl_last_resort(url: str, category: Optional[str], recovered: bool = False) -> dict:
    """The only place Firecrawl is called. Runs after direct, retry, Scrapling, browser/Crawl4AI and index recovery have all failed,
    and only for a JS shell or a challenge that recovery did not resolve. Never for dead domains, never for a lead that already has contact data.
    Gates: key present, paid_policy, LEADX_RUN_CREDIT_CAP / tenant cap, LEADX_FIRECRAWL_RUN_MAX calls per run (25), URL cache.
    Returns {"ok", "html", "markdown", "skipped": reason|None, "cost"}; every real call is counted in fetch_telemetry.counters['firecrawl_calls']."""
    from app.integrations.prod import fetch_telemetry as tel
    from app.integrations.prod import waterfall_runtime as wf
    from app.integrations.prod.fetch_classify import CHALLENGE_CATEGORIES
    from app.integrations.prod.paid_policy import local_mode_denies_paid, paid_fallback_mode
    skip = lambda why: {"ok": False, "html": "", "markdown": "", "skipped": why, "cost": 0.0}
    if recovered:
        return skip("already_recovered")
    if category not in CHALLENGE_CATEGORIES and category not in ("js_shell", "http_403", "http_429"):
        return skip("verdict_not_eligible")   # dns, 404, parked, timeouts and fine pages never reach a paid renderer
    if not (os.getenv("FIRECRAWL_API_KEY") or os.getenv("FIRECRAWL_API_KEYS")):
        return skip("no_key")
    if paid_fallback_mode() == "never" or local_mode_denies_paid():
        return skip("paid_policy")
    if url in _FC_CACHE:
        return {**_FC_CACHE[url], "skipped": "cached", "cost": 0.0}
    c = tel.ctx.get()
    run, tenant = c.get("run_id") or "-", c.get("tenant_id") or ""
    cost = PROVIDERS["firecrawl"]["cost"]
    if _FC_CALLS.get(run, 0) >= int(os.getenv("LEADX_FIRECRAWL_RUN_MAX", "25")):
        return skip("run_call_cap")
    if not wf.budget_allows(run, tenant, cost):
        return skip("run_credit_cap")
    from app.integrations.prod.enricher import fetch_firecrawl
    _FC_CALLS[run] = _FC_CALLS.get(run, 0) + 1
    tel.count("firecrawl_calls")
    r = await fetch_firecrawl(url)
    if not str(r.get("error") or "").startswith("blocked"):   # an SSRF refusal never reached the API
        wf.charge(run, cost)
        await wf.ledger(tenant, run, {"website": url}, "firecrawl", "website", cost, bool(r.get("ok")))
    if len(_FC_CACHE) > 5000:
        _FC_CACHE.pop(next(iter(_FC_CACHE)))
    _FC_CACHE[url] = {"ok": bool(r.get("ok")), "html": r.get("html") or "", "markdown": r.get("markdown") or ""}
    return {**_FC_CACHE[url], "skipped": None, "cost": cost}


def _thin(v: Verdict, html: str) -> bool:
    """A 200 page that is a JS shell or has almost no visible text: a browser may render the real content."""
    n = visible_text_len(html)
    return (v.category == "js_shell" and n < 1200) or (v.category == "ok" and n < 400)


def _retry_after(headers: dict) -> float:
    """Seconds from a Retry-After header; an HTTP-date or garbage value counts as 'long' (no immediate retry)."""
    v = {str(k).lower(): str(x) for k, x in headers.items()}.get("retry-after", "")
    try:
        return float(v)
    except ValueError:
        return 99.0


def _site_side(msg: str) -> bool:
    m = msg.lower()
    return any(s in m for s in ("err_name_not_resolved", "err_connection", "err_cert", "err_ssl", "timeout", "net::", "navigation"))


_engine = Engine()


def get_engine() -> Engine:
    return _engine


async def acquire(url: str, **kw) -> dict:
    return await _engine.acquire(url, **kw)
