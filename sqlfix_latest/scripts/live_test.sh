#!/usr/bin/env bash
# Live integration test for Automiqo Lead Engine + CRM API.
# Run on laptop or VPS after starting the API:
#   cd lead-engine-v2 && ./scripts/live_test.sh
# Optional:
#   API_URL=http://127.0.0.1:8000 ./scripts/live_test.sh
#   FRONTEND_URL=http://127.0.0.1:3000 ./scripts/live_test.sh

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://127.0.0.1:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

PASS=0
FAIL=0
WARN=0
FIXES=()

green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }

ok()   { PASS=$((PASS+1)); green "  ✓ $*"; }
bad()  { FAIL=$((FAIL+1)); red   "  ✗ $*"; FIXES+=("$1"); }
warn() { WARN=$((WARN+1)); yellow "  ⚠ $*"; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    bad "Missing command: $1 — install it to run this test"
    return 1
  fi
  return 0
}

json_get() {
  # json_get <json> <key>  (simple top-level or jq path)
  local json="$1" key="$2"
  if command -v jq >/dev/null 2>&1; then
    echo "$json" | jq -r "$key // empty" 2>/dev/null
  else
    python3 -c "
import json,sys
d=json.loads(sys.argv[1])
k=sys.argv[2].lstrip('.')
parts=k.split('.')
cur=d
for p in parts:
  if isinstance(cur, dict) and p in cur:
    cur=cur[p]
  else:
    cur=''
    break
print(cur if cur is not None else '')
" "$json" "$key" 2>/dev/null
  fi
}

echo ""
bold "═══════════════════════════════════════════════"
bold "  Automiqo Lead Engine — LIVE TEST"
bold "  API: $API_URL"
bold "═══════════════════════════════════════════════"
echo ""

# ── 0. Tools ──────────────────────────────────────────────────────────────
bold "0) Tools"
need_cmd curl || exit 1
if command -v jq >/dev/null 2>&1; then ok "jq available"
elif command -v python3 >/dev/null 2>&1; then ok "python3 available (jq optional)"
else bad "Need jq or python3 to parse JSON"; fi
echo ""

# ── 1. Env file ───────────────────────────────────────────────────────────
bold "1) Environment file"
if [ -f "$ENV_FILE" ]; then
  ok "Found $ENV_FILE"
  # shellcheck disable=SC1090
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE" 2>/dev/null || true
  set +a
else
  bad "No .env at $ENV_FILE — copy .env.example and fill keys"
fi

KEY="${LEAD_ENGINE_SERVICE_KEY:-}"
if [ -n "$KEY" ] && [ "${#KEY}" -ge 16 ]; then
  ok "LEAD_ENGINE_SERVICE_KEY set (${#KEY} chars)"
else
  warn "No service key — OK if ENVIRONMENT=development (open testing)"
fi

if [ -n "${SUPABASE_URL:-}" ]; then ok "SUPABASE_URL set"
else bad "SUPABASE_URL missing — DB will fail"; fi

if [ -n "${SUPABASE_SERVICE_KEY:-}${SUPABASE_SERVICE_ROLE_KEY:-}${SUPABASE_KEY:-}" ]; then
  ok "Supabase service key set"
else
  bad "SUPABASE_SERVICE_KEY (or SERVICE_ROLE_KEY) missing"
fi

if [ -n "${SERPER_API_KEY:-}" ]; then ok "SERPER_API_KEY set (discovery can find leads)"
else warn "SERPER_API_KEY empty — pipeline may return 0 leads (add key or api_key_pool)"; fi

if [ -n "${RESEND_API_KEY:-}" ] || { [ -n "${SMTP_HOST:-}" ] && [ -n "${EMAIL_FROM:-}${SMTP_USER:-}" ]; }; then
  ok "Email provider configured (Resend or SMTP)"
else
  warn "No Resend/SMTP — email send tests will be skipped / fail until configured"
fi
echo ""

# ── 2. API process ────────────────────────────────────────────────────────
bold "2) API reachable"
HTTP_CODE=$(curl -s -o /tmp/lead_health.json -w "%{http_code}" --connect-timeout 5 "$API_URL/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  ok "GET /health → 200"
  HEALTH=$(cat /tmp/lead_health.json)
  OK_FLAG=$(json_get "$HEALTH" ".ok")
  DB_FLAG=$(json_get "$HEALTH" ".checks.db")
  if [ "$OK_FLAG" = "true" ]; then ok "health.ok = true"
  else bad "health.ok is not true — body: $HEALTH"; fi
  if [ "$DB_FLAG" = "true" ]; then ok "Database connected"
  else
    bad "Database not connected (checks.db != true)"
    ERR=$(json_get "$HEALTH" ".checks.db_error")
    [ -n "$ERR" ] && yellow "      db_error: $ERR"
    FIXES+=("Run SUPABASE_FULL_MIGRATION.sql in Supabase SQL Editor; verify SUPABASE_URL + service key")
  fi
else
  bad "API not reachable at $API_URL/health (HTTP $HTTP_CODE)"
  FIXES+=("Start API: cd lead-engine-v2 && docker compose up -d api   OR   uvicorn backend.app_service:app --port 8000")
  echo ""
  bold "Stopped early — start the API and re-run."
  echo ""
  red "FAILED: $FAIL  |  WARNINGS: $WARN  |  PASSED: $PASS"
  exit 1
fi
echo ""

# ── 3. OpenAPI / routes ───────────────────────────────────────────────────
bold "3) API routes mounted"
DOCS=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/docs" 2>/dev/null || echo "000")
if [ "$DOCS" = "200" ]; then ok "Swagger /docs available"
else warn "/docs returned $DOCS (optional)"; fi

# ── 4. Admin auth ─────────────────────────────────────────────────────────
bold "4) Operator auth (service key)"
if [ -z "$KEY" ]; then
  bad "Skip admin tests — no service key"
else
  AUTH_HDR=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")
  # Try list tenants
  TCODE=$(curl -s -o /tmp/lead_tenants.json -w "%{http_code}" \
    "${AUTH_HDR[@]}" "$API_URL/leads/v2/admin/tenants" 2>/dev/null || echo "000")
  if [ "$TCODE" = "200" ]; then
    ok "Admin tenants list works"
  elif [ "$TCODE" = "401" ] || [ "$TCODE" = "403" ]; then
    bad "Admin auth rejected ($TCODE) — check LEAD_ENGINE_SERVICE_KEY matches server env"
  else
    # alternate path
    TCODE2=$(curl -s -o /tmp/lead_tenants.json -w "%{http_code}" \
      "${AUTH_HDR[@]}" "$API_URL/leads/v2/tenants" 2>/dev/null || echo "000")
    if [ "$TCODE2" = "200" ]; then ok "Tenants endpoint works ($TCODE2)"
    else bad "Admin/tenants failed HTTP $TCODE / $TCODE2 — check route mount + key"
    fi
  fi
fi
echo ""

# ── 5. Create or reuse test tenant ────────────────────────────────────────
bold "5) Tenant + ICP"
TENANT_ID=""
ICP_ID=""
if [ -n "$KEY" ]; then
  AUTH_HDR=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")
  CREATE=$(curl -s -o /tmp/lead_tenant_create.json -w "%{http_code}" -X POST \
    "${AUTH_HDR[@]}" "$API_URL/leads/v2/tenants" \
    -d '{"name":"LiveTest Workspace","delivery_channel":"portal","leads_per_month_limit":50,"status":"active"}' \
    2>/dev/null || echo "000")
  BODY=$(cat /tmp/lead_tenant_create.json 2>/dev/null || echo "{}")
  if [ "$CREATE" = "200" ] || [ "$CREATE" = "201" ]; then
    TENANT_ID=$(json_get "$BODY" ".id")
    [ -z "$TENANT_ID" ] && TENANT_ID=$(json_get "$BODY" ".tenant.id")
    [ -z "$TENANT_ID" ] && TENANT_ID=$(json_get "$BODY" ".tenant_id")
    if [ -n "$TENANT_ID" ]; then ok "Created/got tenant $TENANT_ID"
    else
      warn "Tenant create returned $CREATE but no id in body: $BODY"
    fi
  else
    # try list and pick first
    LIST=$(curl -s "${AUTH_HDR[@]}" "$API_URL/leads/v2/admin/tenants" 2>/dev/null || echo "{}")
    TENANT_ID=$(echo "$LIST" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  rows=d.get('tenants') or d if isinstance(d,list) else []
  print(rows[0]['id'] if rows else '')
except: print('')
" 2>/dev/null || true)
    if [ -n "$TENANT_ID" ]; then
      warn "Create tenant HTTP $CREATE — reusing existing $TENANT_ID"
    else
      bad "Cannot create or list tenants (HTTP $CREATE). Body: $BODY"
    fi
  fi

  if [ -n "$TENANT_ID" ]; then
    ICP_BODY=$(curl -s -o /tmp/lead_icp.json -w "%{http_code}" -X POST \
      "${AUTH_HDR[@]}" "$API_URL/leads/v2/icps" \
      -d "{\"tenant_id\":\"$TENANT_ID\",\"name\":\"LiveTest ICP\",\"vertical\":\"medspa\",\"geo\":[\"Hoboken\"],\"keywords\":[\"med spa\"],\"offer_category\":\"lead_gen_service\",\"exclusivity\":\"exclusive\",\"active\":true}" \
      2>/dev/null || echo "000")
    ICP_JSON=$(cat /tmp/lead_icp.json 2>/dev/null || echo "{}")
    if [ "$ICP_BODY" = "200" ] || [ "$ICP_BODY" = "201" ]; then
      ICP_ID=$(json_get "$ICP_JSON" ".id")
      ok "ICP create/ok (id=${ICP_ID:-n/a})"
    else
      warn "ICP create HTTP $ICP_BODY — may already exist; pipeline can use industry+locations. Body: $(echo "$ICP_JSON" | head -c 200)"
    fi
  fi
fi
echo ""

# ── 6. Pipeline dry run ───────────────────────────────────────────────────
bold "6) Pipeline run (small)"
if [ -n "$KEY" ] && [ -n "$TENANT_ID" ]; then
  AUTH_HDR=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")
  PAYLOAD="{\"tenant_id\":\"$TENANT_ID\",\"industry\":\"medspa\",\"locations\":[\"Hoboken\"],\"limit_per_location\":5,\"allow_tier_b\":true,\"deliver\":true"
  [ -n "$ICP_ID" ] && PAYLOAD="$PAYLOAD,\"icp_id\":\"$ICP_ID\""
  PAYLOAD="$PAYLOAD}"
  PCODE=$(curl -s -o /tmp/lead_pipe.json -w "%{http_code}" -X POST \
    "${AUTH_HDR[@]}" "$API_URL/leads/v2/pipeline/run" \
    -d "$PAYLOAD" --max-time 180 2>/dev/null || echo "000")
  PJSON=$(cat /tmp/lead_pipe.json 2>/dev/null || echo "{}")
  if [ "$PCODE" = "200" ]; then
    ok "Pipeline returned 200"
    DMS=$(json_get "$PJSON" ".duration_ms")
    [ -n "$DMS" ] && [ "$DMS" != "null" ] && ok "Pipeline duration_ms=$DMS"
    ERR=$(json_get "$PJSON" ".error")
    if [ -n "$ERR" ] && [ "$ERR" != "null" ]; then
      warn "Pipeline error field: $ERR"
      [ "$ERR" = "quota_exhausted" ] && FIXES+=("Raise leads_per_month_limit on tenant or wait for period reset")
      [ "$ERR" = "discovery_failed" ] && FIXES+=("Check Serper key / network / discovery logs")
    else
      ok "Pipeline completed without top-level error"
    fi
  elif [ "$PCODE" = "000" ]; then
    bad "Pipeline timed out or connection failed — check API logs"
  else
    bad "Pipeline HTTP $PCODE — $(echo "$PJSON" | head -c 300)"
  fi
else
  warn "Skip pipeline — need service key + tenant"
fi
echo ""

# ── 7. CRM search-run + leads ─────────────────────────────────────────────
bold "7) CRM web paths (search-run + leads)"
if [ -n "$TENANT_ID" ]; then
  SCODE=$(curl -s -o /tmp/lead_search.json -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    "$API_URL/crm/$TENANT_ID/search-run" \
    -d '{"vertical":"medspa","geo":["Hoboken"],"keywords":["med spa"],"offer_category":"lead_gen_service","limit_per_location":5}' \
    --max-time 180 2>/dev/null || echo "000")
  SJSON=$(cat /tmp/lead_search.json 2>/dev/null || echo "{}")
  if [ "$SCODE" = "200" ]; then
    ok "POST /crm/{tenant}/search-run → 200"
    SCNT=$(json_get "$SJSON" ".count")
    SDUR=$(json_get "$SJSON" ".duration_sec")
    SEMAIL=$(json_get "$SJSON" ".with_email")
    ok "search-run count=${SCNT:-0} email=${SEMAIL:-0} duration_sec=${SDUR:-?}"
    CNT=$(json_get "$SJSON" ".count")
    ok "Leads returned in response: count=${CNT:-0}"
  else
    bad "search-run HTTP $SCODE — $(echo "$SJSON" | head -c 250)"
    FIXES+=("Ensure crm_api is mounted in app_service and migration applied")
  fi

  LCODE=$(curl -s -o /tmp/lead_crm_leads.json -w "%{http_code}" \
    "$API_URL/crm/$TENANT_ID/leads?limit=10" 2>/dev/null || echo "000")
  if [ "$LCODE" = "200" ]; then ok "GET /crm/{tenant}/leads → 200"
  else warn "GET leads HTTP $LCODE (list may differ by route shape)"; fi

  BCODE=$(curl -s -o /tmp/lead_brand.json -w "%{http_code}" \
    "$API_URL/crm/$TENANT_ID/branding" 2>/dev/null || echo "000")
  if [ "$BCODE" = "200" ]; then ok "GET branding → 200"
  else warn "branding HTTP $BCODE"; fi
else
  warn "Skip CRM tests — no tenant id"
fi
echo ""

# ── 8. Frontend ───────────────────────────────────────────────────────────
bold "8) Frontend (optional)"
FCODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$FRONTEND_URL" 2>/dev/null || echo "000")
if [ "$FCODE" = "200" ] || [ "$FCODE" = "304" ]; then
  ok "Frontend responds at $FRONTEND_URL ($FCODE)"
else
  warn "Frontend not up at $FRONTEND_URL ($FCODE) — run: cd lead-crm-portal/frontend && npm run dev"
fi
echo ""

# ── 9. Security quick check ───────────────────────────────────────────────
bold "9) Security sanity"
if grep -R "NEXT_PUBLIC_SERVICE_KEY\|NEXT_PUBLIC_TENANT_ID" \
  "$ROOT/../lead-crm-portal/frontend" \
  --include="*.ts" --include="*.tsx" 2>/dev/null | grep -v node_modules | head -3 | grep -q .; then
  warn "Found NEXT_PUBLIC_SERVICE_KEY or TENANT_ID refs in frontend — prefer localStorage workspace"
else
  ok "No NEXT_PUBLIC service/tenant key pattern in frontend sources (or portal path missing)"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────
bold "═══════════════════════════════════════════════"
bold "  RESULTS"
bold "═══════════════════════════════════════════════"
green "  Passed:   $PASS"
yellow "  Warnings: $WARN"
red   "  Failed:   $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
  bold "WHAT TO FIX"
  i=1
  # unique-ish
  printf "%s\n" "${FIXES[@]}" | awk 'NF && !seen[$0]++' | while read -r line; do
    echo "  $i. $line"
    i=$((i+1))
  done
  echo ""
  echo "Common fix order:"
  echo "  1) Apply lead-engine-v2/SUPABASE_FULL_MIGRATION.sql in Supabase"
  echo "  2) Fix .env (SUPABASE_* + LEAD_ENGINE_SERVICE_KEY ≥16 chars)"
  echo "  3) Restart API: docker compose up -d api && docker compose logs -f api"
  echo "  4) Add SERPER_API_KEY for real lead volume"
  echo "  5) Start UI: cd lead-crm-portal/frontend && npm run dev"
  echo "  6) Browser: http://localhost:3000 → service key → create workspace → Search"
  echo ""
  exit 1
fi

if [ "$WARN" -gt 0 ]; then
  yellow "Stack is usable but see warnings above (keys / email / frontend)."
  exit 0
fi

green "All critical checks passed. Open the web UI and use Search."
exit 0
