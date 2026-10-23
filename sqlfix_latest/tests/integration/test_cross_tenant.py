"""Optional real-staging cross-tenant suite.

Set LEADX_STAGING_URL, LEADX_TENANT_A_TOKEN, LEADX_TENANT_B_TOKEN,
LEADX_TENANT_A_ID and LEADX_TENANT_B_ID to execute it. Without credentials the
suite is intentionally skipped rather than pretending service-role access proves RLS.
"""
import os, pytest, subprocess, sys

@pytest.mark.integration
def test_cross_tenant_smoke():
    required=['LEADX_STAGING_URL','LEADX_TENANT_A_TOKEN','LEADX_TENANT_B_TOKEN','LEADX_TENANT_A_ID','LEADX_TENANT_B_ID']
    if not all(os.getenv(x) for x in required): pytest.skip('staging credentials not configured')
    r=subprocess.run([sys.executable,'scripts/cross_tenant_smoke.py'],capture_output=True,text=True)
    assert r.returncode==0, r.stdout+r.stderr
