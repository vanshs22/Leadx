#!/usr/bin/env bash
# Local, throwaway test stack: Postgres 16 + PostgREST + LeadX API, then runs "$@" and tears everything down.
#   TOOLS=/path/to/tools PG_BIN=... POSTGREST=... scripts/local_stack/stack.sh python scripts/local_stack/e2e.py
# Test-only: uses fixed local secrets and a shim for Supabase auth/realtime. Never point at production.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
TOOLS="${TOOLS:-/projects/sandbox/.tools}"; PB="${PG_BIN:-$TOOLS/pgenv/bin}"; PGREST="${POSTGREST:-$TOOLS/postgrest}"
PY="${PY:-python3}"; PGPORT="${PGPORT:-54329}"; RESTPORT="${RESTPORT:-54331}"; APIPORT="${APIPORT:-18000}"; GWPORT="${GWPORT:-54330}"
RUNAS=(); [ "$(id -u)" = 0 ] && RUNAS=(setpriv --reuid=65534 --regid=65534 --clear-groups env HOME=/tmp)
export JWT_SECRET="${JWT_SECRET:-local-test-jwt-secret-0123456789abcdef0123456789}"
PSQL=("$PB/psql" -h 127.0.0.1 -p "$PGPORT" -U postgres -q -v ON_ERROR_STOP=1)
cleanup() { [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null; [ -n "${REST_PID:-}" ] && kill "$REST_PID" 2>/dev/null
  "${RUNAS[@]}" "$PB/pg_ctl" -D "$TOOLS/pgdata" stop -m fast >/dev/null 2>&1; }
trap cleanup EXIT
rm -f "$TOOLS/pgdata/postmaster.pid"; mkdir -p "$TOOLS/pglog"
"${RUNAS[@]}" "$PB/pg_ctl" -D "$TOOLS/pgdata" -o "-p $PGPORT -k $TOOLS/pglog -c listen_addresses=127.0.0.1 -c max_connections=300 -c fsync=off" -l "$TOOLS/pglog/pg.log" -w start >/dev/null || { echo "postgres failed"; exit 1; }
if [ "${FRESH_DB:-1}" = 1 ]; then
  "${PSQL[@]}" -c "drop database if exists leadx_e2e" -c "create database leadx_e2e" >/dev/null
  "${PSQL[@]}" -d leadx_e2e -f "$HERE/supabase_shim.sql" >/dev/null 2>&1
  for f in "$ROOT"/supabase/migrations/*.sql; do "${PSQL[@]}" -d leadx_e2e -f "$f" >/dev/null 2>"$TOOLS/mig.err" || { echo "migration failed: $f"; cat "$TOOLS/mig.err"; exit 1; }; done
fi
export SERVICE_JWT="$("$PY" "$HERE/mkjwt.py" "$JWT_SECRET" role=service_role iss=supabase)"
PGRST_DB_URI="postgres://authenticator:authenticator@127.0.0.1:$PGPORT/leadx_e2e" PGRST_DB_SCHEMAS=public PGRST_DB_ANON_ROLE=anon \
  PGRST_JWT_SECRET="$JWT_SECRET" PGRST_SERVER_PORT="$RESTPORT" PGRST_DB_POOL=30 PGRST_DB_MAX_ROWS=100000 "$PGREST" >"$TOOLS/postgrest.log" 2>&1 &
REST_PID=$!
"$PY" "$HERE/gateway.py" "$GWPORT" "$RESTPORT" >"$TOOLS/gw.log" 2>&1 &
"$PY" "$HERE/test_email_validator.py" 54340 >"$TOOLS/validator.log" 2>&1 &
VAL_PID=$!
GW_PID=$!
for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$RESTPORT/" -H "apikey: $SERVICE_JWT" >/dev/null && break; sleep 0.5; done
export LEADX_EMAIL_VALIDATOR_URL="http://127.0.0.1:54340/" LEADX_SMTP_PROBE=0 ALLOW_PRIVATE_OUTBOUND=1 LEADX_DEBUG_TRACEBACKS=1 ENVIRONMENT=development SUPABASE_URL="http://127.0.0.1:$GWPORT" SUPABASE_SERVICE_KEY="$SERVICE_JWT" SUPABASE_ANON_KEY="$SERVICE_JWT" \
  SUPABASE_JWT_SECRET="$JWT_SECRET" LEAD_ENGINE_SERVICE_KEY="local-engine-service-key-0123456789abcdef" API_URL="http://127.0.0.1:$APIPORT" PGPORT TOOLS PB
(cd "$ROOT" && exec "$PY" -m uvicorn app.app_service:app --host 127.0.0.1 --port "$APIPORT" --log-level warning >"$TOOLS/api.log" 2>&1 </dev/null) &
API_PID=$!
(cd "$ROOT" && exec "$PY" -m app.worker --concurrency "${WORKER_CONCURRENCY:-4}" >"$TOOLS/worker.log" 2>&1 </dev/null) &
WORKER_PID=$!
for i in $(seq 1 60); do curl -sf "$API_URL/health" >/dev/null && break; sleep 0.5; done
cd "$ROOT" && "$@"; RC=$?
echo "--- api.log (tail)"; tail -n 15 "$TOOLS/api.log" | cut -c1-240
exit $RC
