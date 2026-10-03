#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$ROOT/k6/load_500.js" ]]; then
  APP_ROOT="$ROOT"
elif [[ -f "$ROOT/sqlfix_latest/k6/load_500.js" ]]; then
  APP_ROOT="$ROOT/sqlfix_latest"
else
  [[ -f "$ROOT/Leadx.zip" ]] || { echo "Leadx.zip not found" >&2; exit 1; }
  unzip -q -o "$ROOT/Leadx.zip" -d "$ROOT"
  if [[ -f "$ROOT/k6/load_500.js" ]]; then APP_ROOT="$ROOT"; else APP_ROOT="$ROOT/sqlfix_latest"; fi
fi

: "${LEADX_URL:?Set LEADX_URL to the staging API URL}"
: "${LEADX_TENANT_ID:?Set LEADX_TENANT_ID to the staging tenant UUID}"
: "${LEADX_TOKEN_FILE:?Set LEADX_TOKEN_FILE to a protected file containing 500 JWTs}"

cd "$APP_ROOT"
exec k6 run --vus "${VUS:-500}" --duration "${DURATION:-10m}" k6/load_500.js "$@"
