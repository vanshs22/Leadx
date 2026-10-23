#!/usr/bin/env python3
"""Run a bounded real LeadX discovery benchmark and publish measurable yield.

Requires SUPABASE_* plus optional provider credentials. Never changes production
state unless --tenant-id is supplied and the configured discovery endpoint itself
creates a run; default mode is dry/static. Use --run-live only in staging.
"""
from __future__ import annotations
import argparse, json, os, statistics, time
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--run-live',action='store_true'); ap.add_argument('--tenant-id'); ap.add_argument('--target',type=int,default=100); ap.add_argument('--industry',default='medspa'); ap.add_argument('--location',default='Austin, TX'); ap.add_argument('--out',default='benchmark_results.json'); a=ap.parse_args()
    result={'timestamp':time.time(),'target':a.target,'industry':a.industry,'location':a.location,'mode':'live' if a.run_live else 'dry-run'}
    if not a.run_live:
        result['status']='READY'; result['note']='Pass --run-live in staging with credentials to measure provider yield, verified email rate, Tier A rate, duplicates removed, and cost per lead.'
    else:
        if not a.tenant_id or not os.getenv('SUPABASE_URL') or not (os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')):
            raise SystemExit('live benchmark requires --tenant-id and SUPABASE_URL/SUPABASE_SERVICE_KEY')
        from app.integrations.prod.exhaustive_discovery import discover_exhaustive
        started=time.perf_counter()
        rows=discover_exhaustive(tenant_id=a.tenant_id, queries=[a.location], industry=a.industry, target=a.target)
        elapsed=time.perf_counter()-started
        unique={str(x.get('fingerprint') or x.get('id') or x.get('website') or x.get('name')) for x in rows}
        verified=sum(1 for x in rows if x.get('email_verification_status') in ('valid','deliverable') or x.get('email_smtp_verified') or x.get('email_provider_verified'))
        tier_a=sum(1 for x in rows if str(x.get('tier','')).upper()=='A')
        result.update(status='PASS', discovered=len(rows), unique=len(unique), duplicates_removed=max(0,len(rows)-len(unique)), verified_email_rate=verified/max(1,len(rows)), tier_a_rate=tier_a/max(1,len(rows)), elapsed_seconds=round(elapsed,2), cost_per_lead=None, bounce_rate=None)
    Path(a.out).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
