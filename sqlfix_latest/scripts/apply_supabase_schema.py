"""Apply the LeadX migration chain to a Supabase (or any Postgres) database, optionally resetting `public` first.

usage:
  SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres' \\
      python scripts/apply_supabase_schema.py [--reset --confirm-ref <ref>]

Use the SESSION pooler (port 5432) or the direct connection; the transaction pooler (6543) also works for this script.
--reset drops the whole `public` schema (all LeadX data) and recreates it with the standard Supabase grants. It only runs
when --confirm-ref equals the project ref in the connection user (postgres.<ref>) or, for plain Postgres, the database name.
Auth users (schema auth) and Storage are not touched. Each migration runs in its own transaction; the first failure stops the run.
"""
import glob
import os
import sys
import time
from urllib.parse import urlparse

import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESET_SQL = """
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT USAGE ON SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT ALL ON SCHEMA public TO postgres, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO postgres, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO postgres, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO postgres, service_role;
"""


def project_ref(dsn: str) -> str:
    u = urlparse(dsn)
    user = u.username or ""
    return user.split(".", 1)[1] if "." in user else (u.path or "/").lstrip("/")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("set SUPABASE_DB_URL")
        return 2
    args = sys.argv[1:]
    reset = "--reset" in args
    confirm = args[args.index("--confirm-ref") + 1] if "--confirm-ref" in args else ""
    ref = project_ref(dsn)
    if reset and confirm != ref:
        print(f"refusing to reset: --confirm-ref must equal '{ref}' (the project ref / database in the connection string)")
        return 2
    files = sorted(glob.glob(os.path.join(ROOT, "supabase", "migrations", "*.sql")))
    conn = psycopg2.connect(dsn, connect_timeout=20)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("select version(), current_user")
    print("connected:", cur.fetchone())
    if reset:
        cur.execute(RESET_SQL)
        conn.commit()
        print("public schema reset")
    for i, path in enumerate(files, 1):
        name = os.path.basename(path)
        t0 = time.time()
        try:
            cur.execute(open(path, encoding="utf-8").read().lstrip("\ufeff"))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED {i}/{len(files)} {name}: {type(exc).__name__}: {str(exc)[:500]}")
            return 1
        print(f"ok {i:02d}/{len(files)} {name} ({time.time() - t0:.1f}s)")
    cur.execute("select count(*) from information_schema.tables where table_schema = 'public'")
    print("public tables:", cur.fetchone()[0])
    try:
        cur.execute("notify pgrst, 'reload schema'")
        conn.commit()
        print("asked PostgREST to reload its schema cache")
    except Exception as exc:
        print("schema reload notify skipped:", type(exc).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
