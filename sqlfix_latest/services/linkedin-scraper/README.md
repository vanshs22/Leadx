# LinkedIn scraper (low-memory)

Linux VPS friendly: **one shared headless Chromium**, no images, concurrency 1, `mem_limit: 512m`.

## Start (with main stack)

```bash
cd lead-engine-v2
docker compose --profile social up -d --build linkedin-scraper
curl http://127.0.0.1:8001/health
```

## Cookie

Add to Supabase `api_key_pool`:

| field | value |
|-------|--------|
| provider | `linkedin_scraper` |
| key_value | your `li_at` cookie |
| active | true |

## Env

```bash
LINKEDIN_SCRAPER_URL=http://linkedin-scraper:8001
# same host without docker network:
# LINKEDIN_SCRAPER_URL=http://127.0.0.1:8001
SOCIAL_ENRICH_MAX_PER_RUN=10
```

Typical RAM: **300–500 MB**. Prefer a small second VPS if API already uses most of 2 GB.
