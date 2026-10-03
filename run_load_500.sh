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

[[ -f "$LEADX_TOKEN_FILE" ]] || { echo "Token file not found: $LEADX_TOKEN_FILE" >&2; echo "Create a real path containing exactly 500 staging JWTs, one per line." >&2; exit 1; }
if [[ "$(stat -c '%a' "$LEADX_TOKEN_FILE" 2>/dev/null || stat -f '%Lp' "$LEADX_TOKEN_FILE")" != "600" ]]; then
  echo "Token file must have permissions 600: chmod 600 \"$LEADX_TOKEN_FILE\"" >&2
  exit 1
fi
token_count="$(awk 'NF && $1 !~ /^#/ {count++} END {print count+0}' "$LEADX_TOKEN_FILE")"
[[ "$token_count" == "500" ]] || { echo "Token file must contain exactly 500 non-empty tokens; found $token_count" >&2; exit 1; }

K6_BIN="${K6_BIN:-$(type -P k6 2>/dev/null || true)}"
if [[ -z "$K6_BIN" || ! -x "$K6_BIN" || -d "$K6_BIN" ]]; then
  echo "k6 is not installed or is resolving to a directory." >&2
  echo "Install k6, then verify with: command -v k6" >&2
  exit 1
fi

cd "$APP_ROOT"
exec "$K6_BIN" run --vus "${VUS:-500}" --duration "${DURATION:-10m}" k6/load_500.js "$@"
