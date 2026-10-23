"""Release verification for LeadX 3.0.
Run from lead-engine-v2 with Python dependencies installed.
"""
from __future__ import annotations
import glob, re, os, subprocess, sys, pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    py=glob.glob('app/**/*.py',recursive=True)
    assert py, 'no backend source'
    subprocess.run([sys.executable,'-m','compileall','-q','app','tests'],check=True)
    r=subprocess.run([sys.executable,'-m','pytest','-q'],capture_output=True,text=True,check=True)
    print(r.stdout.strip().splitlines()[-1])
    import app.app_service as service
    assert service.app.version == '3.0.0'
    assert len(service.app.routes) >= 240
    forbidden=[]
    for root,_,files in os.walk('.'):
        if root.startswith('./.pytest_cache'): continue
        for f in files:
            if f.endswith(('.py','.ts','.tsx','.sql','.md','.json')):
                p=os.path.join(root,f)
                if p.endswith('scripts/verify_leadx_3_0.py'): continue
                try:s=open(p,encoding='utf8').read()
                except Exception:continue
                if 'sk-proj-' in s or re.search(r'AKIA[0-9A-Z]{12,}', s) or re.search(r'LEAD_ENGINE_SERVICE_KEY=.{16,}', s): forbidden.append(p)
    assert not forbidden, f'credential-like content in {forbidden}'
    print(f'LeadX 3.0 verification OK: {len(service.app.routes)} routes, {len(py)} Python files')

if __name__=='__main__': main()
