#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "==> Stopping stale LeadX frontend processes"
pkill -f 'next dev.*3000' 2>/dev/null || true
echo "==> Cleaning Next/TypeScript build cache"
npm run clean
echo "==> Starting LeadX frontend on :3000"
exec npm run dev -- --hostname 0.0.0.0
