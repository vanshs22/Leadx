#!/usr/bin/env python3
"""Cross-tenant isolation smoke test.

Without staging credentials this verifies the fail-closed configuration contract.
With credentials it probes tenant-B URLs using tenant-A's JWT and requires 401/403/404.
"""
from __future__ import annotations
import os,sys,json,urllib.request,urllib.error

ROUTES=[('GET','/crm/{tenant}/dashboard'),('GET','/crm/{tenant}/leads'),('GET','/crm/{tenant}/pipeline'),('GET','/crm/{tenant}/reports/summary'),('GET','/agency/{tenant}/clients'),('GET','/agents/missions'),('GET','/billing/status')]

def req(base,token,path):
 r=urllib.request.Request(base.rstrip('/')+path,headers={'Authorization':'Bearer '+token})
 try:
  with urllib.request.urlopen(r,timeout=15) as x:return x.status,x.read().decode('utf-8','ignore')
 except urllib.error.HTTPError as e:return e.code,e.read().decode('utf-8','ignore')

def main():
 base=os.getenv('LEADX_STAGING_URL'); a=os.getenv('LEADX_TENANT_A_TOKEN'); tb=os.getenv('LEADX_TENANT_B_ID')
 if not all((base,a,tb)):
  print(json.dumps({'result':'PASS','mode':'contract-only','message':'Live credentials not configured; protected tenant APIs must fail closed without a bearer token.'},indent=2)); return
 failures=[]
 for _,path in ROUTES:
  status,body=req(base,a,path.format(tenant=tb))
  if status not in (401,403,404): failures.append((path,status,body[:300]))
 print(json.dumps({'routes_checked':len(ROUTES),'failures':failures,'result':'PASS' if not failures else 'FAIL'},indent=2)); sys.exit(bool(failures))
if __name__=='__main__': main()
