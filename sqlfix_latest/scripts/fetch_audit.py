"""Website acquisition audit and before/after benchmark on real business sites.

  python scripts/fetch_audit.py collect --n 450            # sample real business sites from Overture (several cities/industries) -> artifacts/fetch_audit/sites.json
  python scripts/fetch_audit.py audit   [--limit N]        # failure audit: classify what each provider really returned, per status/vendor/domain
  python scripts/fetch_audit.py bench   [--limit N] [--arms legacy,fast,engine]   # enrich_one on the same sites, per arm
  python scripts/fetch_audit.py report                      # write docs/FETCH_AUDIT.md from the JSON results

Arms: legacy = Scrapling-first fetch (LEADX_FETCH_FAST=0, no engine; the original behaviour), fast = pooled-httpx-first without the engine
(the state before this change), engine = acquisition engine + alternative-source recovery. Free path only: LEADX_PAID_FALLBACK=never,
no provider keys are read. Arms run one after another against the same live sites; the engine arm runs last, so any site-side rate
limiting caused by the earlier arms works against it.
"""
import argparse, asyncio, json, os, random, sys, time
from collections import Counter, defaultdict
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LEADX_PAID_FALLBACK", "never")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "fetch_audit")
os.makedirs(OUT, exist_ok=True)
PLAN = [("Houston, TX", "dentist"), ("Austin, TX", "plumber"), ("Denver, CO", "hair salon"), ("Phoenix, AZ", "lawyer"), ("Atlanta, GA", "hvac"),
        ("Chicago, IL", "dentist"), ("Seattle, WA", "gym"), ("Miami, FL", "med spa"), ("Dallas, TX", "roofer"), ("Boston, MA", "chiropractor")]


def _jl(name, data=None):
    p = os.path.join(OUT, name)
    if data is None:
        return json.load(open(p))
    json.dump(data, open(p, "w"), indent=1, default=str)


def _p(n, d):
    return round(100 * n / d, 1) if d else None


async def collect(n):
    from app.integrations.prod.open_data import overture_places
    sites, seen = [], set()
    per = max(10, n // len(PLAN) + 25)
    for loc, ind in PLAN:
        try:
            rows = await overture_places(ind, loc)
        except Exception as exc:
            print("skip", loc, ind, type(exc).__name__); continue
        rows = [r for r in rows if r.get("website")]
        random.Random(7).shuffle(rows)
        for r in rows[:per]:
            host = (urlparse(r["website"]).hostname or "").lower().removeprefix("www.")
            if host in seen:
                continue
            seen.add(host)
            city = loc.split(",")[0]
            sites.append({"name": r["name"], "website": r["website"], "city": city, "region": loc.split(",")[-1].strip(), "phone": r.get("phone"), "email": r.get("email"),
                          "address": r.get("address"), "category": r.get("category"), "source": "overture", "industry": ind, "socials": r.get("socials") or []})
        print(loc, ind, len(rows), "with website; total", len(sites), flush=True)
    random.Random(11).shuffle(sites)
    _jl("sites.json", sites[:n])
    print("saved", len(sites[:n]))


async def audit(limit):
    """Per site: direct fetch (what every earlier path did first), then Scrapling and the local browser on every site direct could not use."""
    from app.integrations.prod import fetch_engine as fe
    from app.integrations.prod.fetch_classify import classify_response
    sites = _jl("sites.json")[:limit]
    eng = fe.Engine()
    sem = asyncio.Semaphore(48)
    rows = []

    async def one(s):
        async with sem:
            url = s["website"]
            raw, v, atts = await eng._direct_with_fixes(url, 20)
            row = {"site": s["website"], "domain": fe.registered_domain(url), "city": s["city"], "industry": s["industry"], "direct": {"category": v.category, "kind": v.kind, "status": raw.get("status"),
                   "usable": v.usable, "ms": atts[-1]["ms"], "attempts": len(atts), "error": (raw.get("error") or "")[:120],
                   "server": (raw.get("headers") or {}).get("server") or (raw.get("headers") or {}).get("Server")}, "others": {}}
            if not v.usable:
                for prov in ("scrapling", "browser"):
                    if prov == "browser" and v.kind in ("challenge", "rate_limit"):
                        # measured once on a small sample below; the engine itself never escalates a challenge
                        continue
                    r2, v2, a2 = await eng.run_provider(prov, url, v.category)
                    row["others"][prov] = {"category": v2.category, "usable": v2.usable, "status": r2.get("status"), "ms": a2["ms"], "error": (r2.get("error") or "")[:100]}
            rows.append(row)

    t = time.monotonic()
    await asyncio.gather(*(one(s) for s in sites))
    # challenge sites: does a real browser get a different answer? (measurement only, 25 sites, not an engine path)
    probe = [r for r in rows if r["direct"]["kind"] in ("challenge", "rate_limit")][:25]
    for r in probe:
        r2, v2, a2 = await eng.run_provider("browser", r["site"], r["direct"]["category"])
        r["others"]["browser_probe"] = {"category": v2.category, "usable": v2.usable, "status": r2.get("status"), "ms": a2["ms"]}
    await eng.close()
    _jl("audit.json", {"n": len(rows), "wall_s": round(time.monotonic() - t, 1), "rows": rows})
    summarize_audit(rows)


def summarize_audit(rows):
    n = len(rows)
    cat = Counter(r["direct"]["category"] for r in rows)
    print(f"\n{n} sites; direct usable {sum(r['direct']['usable'] for r in rows)} ({_p(sum(r['direct']['usable'] for r in rows), n)}%)")
    for k, v in cat.most_common():
        print(f"  {k:20s} {v:4d} {_p(v, n)}%")


def _local_plane():
    """Telemetry goes to the SQLite demo plane (same setup as benchmark_pipeline.py) so the dashboard endpoint can be read back."""
    import tempfile
    os.environ.update(LOCAL_VALIDATION_MODE="true", LOCAL_DURABLE_BACKEND="sqlite", LOCAL_DURABLE_PATH=os.getenv("BENCH_DB", f"{tempfile.mkdtemp(prefix='leadx-fetch-')}/db.sqlite3"), LOCAL_AUTH_TENANT_ID="bench-tenant")
    from app.memory import supabase_client
    db = supabase_client.get_supabase()
    os.environ["LOCAL_VALIDATION_MODE"] = "false"
    supabase_client._client = db
    return db


async def bench(limit, arms):
    _local_plane()
    from app.integrations.prod import enricher, fetch_engine as fe
    from app.integrations.prod.fetch_classify import classify_response
    sites = _jl("sites.json")[:limit]
    results = {}
    for arm in arms:
        os.environ["LEADX_FETCH_ENGINE"] = "1" if arm == "engine" else "0"
        os.environ["LEADX_FETCH_FAST"] = "0" if arm == "legacy" else "1"
        fe._engine = fe.Engine()
        seen_home: dict = {}
        orig = enricher.fetch_scrapling

        async def traced(url, timeout=20, light=False, _o=orig):
            r = await _o(url, timeout, light)
            if not light:
                v = classify_response(r.get("status") if r.get("status") else (200 if r.get("ok") else None), r.get("html") or "", None, r.get("error"))
                seen_home.setdefault(url, {"category": r.get("category") or v.category, "usable": bool(r.get("ok")) and v.usable, "method": r.get("method"), "status": r.get("status"),
                                           "direct_first": (r.get("attempts") or [{}])[0].get("ok") if r.get("attempts") else None})
            return r
        enricher.fetch_scrapling = traced
        sem = asyncio.Semaphore(int(os.getenv("BENCH_CONC", "48")))
        out = []

        async def one(s):
            async with sem:
                t = time.monotonic()
                from app.integrations.prod import fetch_telemetry
                fetch_telemetry.ctx.set({"tenant_id": "bench-tenant", "run_id": f"bench-{arm}", "discovery_source": s["source"], "lead_key": s["website"]})
                try:
                    info = await asyncio.wait_for(enricher.enrich_one(s["website"], use_crawl4ai=False, use_firecrawl=False, company_name=s["name"], city=s["city"],
                                                                      hints={"phone": s["phone"], "email": s["email"], "address": s["address"], "category": s["category"], "region": s["region"]}), 150)
                except Exception as exc:
                    info = {"error": type(exc).__name__}
                url = enricher._normalize_website_url(s["website"])
                out.append({"site": s["website"], "domain": fe.registered_domain(s["website"]), "secs": round(time.monotonic() - t, 2), "home": seen_home.get(url) or {}, "info": slim(info)})
        t0 = time.monotonic()
        await asyncio.gather(*(one(s) for s in sites))
        enricher.fetch_scrapling = orig
        from app.integrations.prod import fetch_telemetry
        fetch_telemetry.flush()
        await fe._engine.close()
        results[arm] = {"wall_s": round(time.monotonic() - t0, 1), "rows": out}
        print(arm, "done", results[arm]["wall_s"], "s", flush=True)
        _jl("bench.json", results)
    summarize_bench(results)


def slim(info):
    raw = info.get("raw_extraction") or {}
    a = info.get("acquisition") or {}
    return {"ok": bool(info.get("enrichment_ok")), "error": info.get("error"), "email": info.get("email"), "phone": info.get("phone_from_web"),
            "owner": info.get("owner_name"), "social": bool(info.get("social_links")), "address": bool(raw.get("address_parsed")),
            "class": a.get("class"), "cost": a.get("cost", 0.0), "tokens": a.get("tokens", 0), "completeness": a.get("completeness"), "recovery": (a.get("recovery") or {}).get("found"),
            "challenge": a.get("challenge"), "category": a.get("category"), "profile": sorted((info.get("profile") or {})), "fields": a.get("fields"),
            "method": info.get("enrichment_method")}


def arm_metrics(rows):
    n = len(rows)
    fetched = sum(1 for r in rows if r["home"].get("usable"))
    direct_first = sum(1 for r in rows if r["home"].get("usable") and (r["home"].get("method") in ("direct", "httpx", "scrapling")))
    cls = Counter(r["info"].get("class") or ("fetched" if r["home"].get("usable") else "failed") for r in rows)
    recovered = cls.get("recovered", 0)
    site_contact = sum(1 for r in rows if r["home"].get("usable") and (r["info"].get("email") or r["info"].get("phone")))
    email_site = sum(1 for r in rows if r["home"].get("usable") and r["info"].get("email"))
    any_contact = sum(1 for r in rows if (r["home"].get("usable") and (r["info"].get("email") or r["info"].get("phone"))) or r["info"].get("class") == "recovered")
    core = ("email", "phone", "owner", "social", "address")
    comp = [sum(1 for f in core if r["info"].get(f)) / len(core) for r in rows]
    return {"n": n, "website_fetch_success_pct": _p(fetched, n), "acquisition_success_pct": _p(fetched + recovered, n), "alt_recovered": recovered, "alt_recovered_pct": _p(recovered, n),
            "hint_only": cls.get("hint_only", 0), "failed": cls.get("failed", 0), "enriched_ok_pct": _p(sum(1 for r in rows if r["info"].get("ok")), n),
            "site_email_pct": _p(email_site, n), "contact_from_site_pct": _p(site_contact, n), "usable_contact_pct": _p(any_contact, n),
            "avg_core_completeness_pct": round(100 * sum(comp) / n, 1) if n else None, "avg_latency_s": round(sum(r["secs"] for r in rows) / n, 2) if n else None,
            "provider_cost_credits": round(sum(r["info"].get("cost") or 0 for r in rows), 3), "tokens": sum(r["info"].get("tokens") or 0 for r in rows)}


def summarize_bench(results):
    for arm, d in results.items():
        print(arm, json.dumps({**arm_metrics(d["rows"]), "wall_s": d["wall_s"]}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "audit", "bench", "report"])
    ap.add_argument("--n", type=int, default=450)
    ap.add_argument("--limit", type=int, default=10_000)
    ap.add_argument("--arms", default="legacy,fast,engine")
    a = ap.parse_args()
    if a.cmd == "collect":
        asyncio.run(collect(a.n))
    elif a.cmd == "audit":
        asyncio.run(audit(a.limit))
    elif a.cmd == "bench":
        asyncio.run(bench(a.limit, a.arms.split(",")))
    else:
        from scripts.fetch_audit_report import write_report  # noqa: E402
        write_report(OUT)
