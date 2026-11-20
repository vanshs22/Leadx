"""End-to-end timing on the local SQLite data plane (no Supabase): discovery -> enrichment -> score -> assign -> CRM/delivery.

usage: python scripts/benchmark_pipeline.py "Houston, TX" dentist 100
Free path only (no provider keys are read). Prints wall time, stage counts and the run summary.
"""
import asyncio, json, os, sys, tempfile, time, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
d = tempfile.mkdtemp(prefix="leadx-bench-")
os.environ.update(LOCAL_VALIDATION_MODE="true", LOCAL_DURABLE_BACKEND="sqlite", LOCAL_DURABLE_PATH=os.getenv("BENCH_DB", f"{d}/db.sqlite3"),
                  LOCAL_AUTH_TENANT_ID="bench-tenant")
os.environ.setdefault("LEADX_PAID_FALLBACK", "never")  # export LEADX_PAID_FALLBACK=auto plus SERPER_API_KEY / FIRECRAWL_API_KEY to include paid fallbacks
from app.memory import supabase_client  # noqa: E402
_db = supabase_client.get_supabase()               # sqlite client, built while local mode is on
os.environ["LOCAL_VALIDATION_MODE"] = "false"      # local mode also blocks the network and swaps in synthetic discovery
supabase_client._client = _db
from app.integrations.prod.pipeline import run_pipeline  # noqa: E402


async def main(loc, industry, n):
    from app.memory.supabase_client import get_supabase
    get_supabase().table("tenants").upsert({"id": "bench-tenant", "name": "bench", "status": "active", "plan": "enterprise", "leads_per_month_limit": 100000}, on_conflict="id").execute()
    t = time.monotonic()
    out = await run_pipeline("bench-tenant", industry=industry, locations=[loc], target_new_leads=n, limit_per_location=n,
                             run_id=str(uuid.uuid4()), use_crawl4ai=False, use_firecrawl=False, max_enrich_concurrent=int(os.getenv("BENCH_CONC", "32")),
                             deliver=True, include_web=False, include_yelp=False, include_facebook=False)
    c = out.get("counts") or out
    print(json.dumps({"wall_s": round(time.monotonic() - t, 1), "stage_timings": out.get("stage_timings"), "stop_reason": out.get("stop_reason"),
                      "first": {k: (out.get("lane_telemetry") or {}).get(k) for k in ("first_candidate_seconds", "first_enriched_seconds", "first_assigned_seconds")},
                      "fast_seed": (out.get("lane_telemetry") or {}).get("fast_seed"), "latency": (out.get("lane_telemetry") or {}).get("latency_samples"),
                      "counts": {k: c.get(k) for k in ("discovered", "enriched", "gated", "assigned", "delivered")},
                      "authoritative_counts": out.get("authoritative_counts")}, indent=1, default=str))

asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
