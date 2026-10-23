#!/usr/bin/env python3
"""Single production-gate orchestrator. External gates are explicit, never faked."""
from __future__ import annotations
import os, subprocess, sys

def run(cmd, env=None, required=True, cwd=None):
    print('\n$',' '.join(cmd),flush=True); r=subprocess.run(cmd,env=env,cwd=cwd); 
    if r.returncode and required: raise SystemExit(r.returncode)
    return r.returncode

def main():
    py=sys.executable
    import importlib.util, shutil
    pytest_cmd=[py,'-m','pytest','-q'] if importlib.util.find_spec('pytest') is not None else ([shutil.which('pytest'),'-q'] if shutil.which('pytest') else None)
    commands = [
        [py,'-m','compileall','-q','app','services','tests'],
        [py,'scripts/check_migrations.py'],
        [py,'scripts/security_release_gate.py'],
        [py,'scripts/check_frontend_accessibility.py'],
        [py,'scripts/check_rls_coverage.py'],
        [py,'scripts/verify_runtime_schema.py'],
        [py,'scripts/check_plan_contract.py'],
        [py,'scripts/release_audit.py'],
    ]
    if pytest_cmd:
        commands.append(pytest_cmd)
    else:
        print('\nSKIP pytest — no pytest executable is available; install requirements-dev.txt')
    for cmd in commands: run(cmd)
    if os.getenv('RUN_FRONTEND_GATE')=='1':
        run(['npm','ci'], cwd='lead-crm-portal')
        run(['npx','tsc','--noEmit'], cwd='lead-crm-portal')
        run(['npm','run','build'], cwd='lead-crm-portal')
    if os.getenv('RUN_CROSS_TENANT')=='1': run([py,'scripts/cross_tenant_smoke.py'])
    if os.getenv('RUN_LIVE_BENCHMARK')=='1': run([py,'scripts/engine_benchmark.py','--run-live','--tenant-id',os.environ['LEADX_BENCHMARK_TENANT_ID']])
    staging = all(os.getenv(k) for k in ('LEADX_STAGING_URL','LEADX_TENANT_A_TOKEN','LEADX_TENANT_B_ID'))
    print('\nSTAGING LIVE GATES:', 'CONFIGURED' if staging else 'NOT CONFIGURED (not claimed as passed)')
    print('LOCAL/ENV GATE COMPLETE')
if __name__=='__main__':main()
