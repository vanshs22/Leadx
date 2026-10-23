#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if curl -sf http://127.0.0.1:8001/health >/dev/null 2>&1; then
  echo "Already running on :8001"
  exit 0
fi
if ! command -v docker >/dev/null; then
  echo "Docker required for LinkedIn scraper"
  exit 1
fi
docker rm -f leadx-linkedin 2>/dev/null || true
docker build -t leadx-linkedin-scraper .
docker run -d --name leadx-linkedin --restart unless-stopped \
  -p 8001:8001 --memory=512m --cpus=1 \
  -e CHROME_SINGLE_PROCESS=1 \
  leadx-linkedin-scraper
sleep 2
curl -sf http://127.0.0.1:8001/health && echo " OK"
