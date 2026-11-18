"""LOCAL TEST stand-in for the Supabase Auth (GoTrue) endpoints the portal and API call:
POST /auth/v1/token?grant_type=password|refresh_token, POST /auth/v1/signup, GET /auth/v1/user, POST /auth/v1/logout.
Passwords live in auth.users.encrypted_password (bcrypt via pgcrypto). Signup auto-confirms the email (no mail is sent).
Test stack only: it signs tokens with the local test JWT secret."""
import json, os, time, uuid
import psycopg2
from jose import jwt

SECRET = os.environ["JWT_SECRET"]
DSN = f"host=127.0.0.1 port={os.environ.get('PGPORT', '54329')} user=postgres dbname=leadx_e2e"


def _db():
    c = psycopg2.connect(DSN); c.autocommit = True
    return c


def _user_json(r):
    return {"id": str(r[0]), "aud": "authenticated", "role": "authenticated", "email": r[1], "email_confirmed_at": r[2].isoformat() if r[2] else None,
            "user_metadata": r[3] or {}, "app_metadata": {"provider": "email"}, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _session(r):
    now = int(time.time())
    claims = {"sub": str(r[0]), "email": r[1], "role": "authenticated", "aud": "authenticated", "iss": "local-auth-shim", "iat": now,
              "email_verified": bool(r[2]), "email_confirmed_at": r[2].isoformat() if r[2] else None, "user_metadata": r[3] or {}}
    access = jwt.encode({**claims, "exp": now + 3600}, SECRET, algorithm="HS256")
    refresh = jwt.encode({"sub": str(r[0]), "typ": "refresh", "aud": "authenticated", "exp": now + 7 * 86400, "jti": uuid.uuid4().hex}, SECRET, algorithm="HS256")
    return {"access_token": access, "token_type": "bearer", "expires_in": 3600, "expires_at": now + 3600, "refresh_token": refresh, "user": _user_json(r)}


def _by_email(cur, email):
    cur.execute("select id,email,email_confirmed_at,raw_user_meta_data from auth.users where lower(email)=lower(%s)", (email,)); return cur.fetchone()


def handle(method, path, query, headers, body):
    """returns (status, json)"""
    p = path[len("/auth/v1"):]
    try:
        data = json.loads(body) if body else {}
    except Exception:
        return 400, {"msg": "invalid json"}
    with _db() as c, c.cursor() as cur:
        if p == "/health":
            return 200, {"name": "local-auth-shim"}
        if p == "/token" and method == "POST":
            gt = (query.get("grant_type") or [""])[0]
            if gt == "password":
                cur.execute("select id,email,email_confirmed_at,raw_user_meta_data from auth.users where lower(email)=lower(%s) and encrypted_password = crypt(%s, encrypted_password)", (data.get("email", ""), data.get("password", "")))
                r = cur.fetchone()
                return (200, _session(r)) if r else (400, {"error": "invalid_grant", "error_description": "Invalid login credentials", "msg": "Invalid login credentials"})
            if gt == "refresh_token":
                try:
                    t = jwt.decode(data.get("refresh_token", ""), SECRET, algorithms=["HS256"], audience="authenticated")
                    assert t.get("typ") == "refresh"
                except Exception:
                    return 400, {"error": "invalid_grant", "msg": "Invalid Refresh Token"}
                cur.execute("select id,email,email_confirmed_at,raw_user_meta_data from auth.users where id=%s", (t["sub"],)); r = cur.fetchone()
                return (200, _session(r)) if r else (400, {"msg": "user not found"})
            return 400, {"msg": "unsupported grant_type"}
        if p == "/signup" and method == "POST":
            email, pw = str(data.get("email", "")).strip().lower(), str(data.get("password", ""))
            if "@" not in email or len(pw) < 8:
                return 422, {"msg": "Signup requires a valid email and a password of at least 8 characters", "error_code": "weak_password"}
            if _by_email(cur, email):
                return 422, {"msg": "User already registered", "error_code": "user_already_exists"}
            cur.execute("insert into auth.users(email, encrypted_password, email_confirmed_at, raw_user_meta_data) values (lower(%s), crypt(%s, gen_salt('bf')), now(), %s::jsonb) returning id,email,email_confirmed_at,raw_user_meta_data",
                        (email, pw, json.dumps(data.get("data") or {})))
            return 200, _session(cur.fetchone())
        if p == "/user" and method == "GET":
            tok = (headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            try:
                t = jwt.decode(tok, SECRET, algorithms=["HS256"], audience="authenticated")
            except Exception:
                return 401, {"msg": "invalid JWT"}
            cur.execute("select id,email,email_confirmed_at,raw_user_meta_data from auth.users where id=%s", (t["sub"],)); r = cur.fetchone()
            return (200, _user_json(r)) if r else (401, {"msg": "user not found"})
        if p == "/logout":
            return 204, None
    return 404, {"msg": "not implemented in the local auth shim"}


def set_password(email, password, tenant_meta=None):
    """test bootstrap helper: create or reset a user with a password"""
    with _db() as c, c.cursor() as cur:
        cur.execute("insert into auth.users(email, encrypted_password, email_confirmed_at, raw_user_meta_data) values (lower(%s), crypt(%s, gen_salt('bf')), now(), %s::jsonb) "
                    "on conflict (email) do update set encrypted_password = excluded.encrypted_password returning id", (email, password, json.dumps(tenant_meta or {})))
        return str(cur.fetchone()[0])
