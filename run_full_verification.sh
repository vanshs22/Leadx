#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$ROOT/scripts/full_verification.py" ]]; then
  APP_ROOT="$ROOT"
elif [[ -f "$ROOT/sqlfix_latest/scripts/full_verification.py" ]]; then
  APP_ROOT="$ROOT/sqlfix_latest"
else
  [[ -f "$ROOT/Leadx.zip" ]] || { echo "Leadx.zip not found" >&2; exit 1; }
  unzip -q -o "$ROOT/Leadx.zip" -d "$ROOT"
  if [[ -f "$ROOT/scripts/full_verification.py" ]]; then APP_ROOT="$ROOT"; else APP_ROOT="$ROOT/sqlfix_latest"; fi
fi

cd "$APP_ROOT"
exec python3 scripts/full_verification.py "$@"
