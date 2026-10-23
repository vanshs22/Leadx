import os, sys, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update({"SUPABASE_URL": "https://t.supabase.co", "SUPABASE_SERVICE_KEY": "k", "ENVIRONMENT": "development",
                   "LEADX_PAID_FALLBACK": "never", "LEADX_SMTP_PROBE": "0"})
from tests.rig import stubs; stubs.install()
from tests.rig.fakedb import FakeDB
import app.memory.supabase_client as sc
DB = FakeDB()
sc.get_supabase = lambda: DB
def uid(): return str(uuid.uuid4())
def seed_tenant():
    t = uid(); DB.seed("tenants", {"id": t, "name": "Acme", "plan": "free"}); return t
