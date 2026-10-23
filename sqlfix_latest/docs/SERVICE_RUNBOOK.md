# Managed Lead Service — Production Runbook

**Model:** You operate the engine. Clients receive exclusive, enriched leads + a CRM to work them.  
Clients do **not** trigger scrapes.

---

## 1. One-time setup

### Database
Run migrations in order on Supabase:
```
002_lead_engine_v2_production.sql
003_crm_portal_schema.sql   # from lead-crm-portal
004_rls_and_auth.sql
005_outcomes_feedback.sql
006_next_tier.sql
007_team_branding_ops.sql
008_stripe_billing.sql
009_social_enrich.sql
```

### Env
```bash
cp .env.example .env
# Set at minimum:
# LEAD_ENGINE_SERVICE_KEY, SUPABASE_*, DELIVERY_CONFIG_KEY
```

Generate delivery key:
```bash
python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

### Deploy API
```bash
docker compose up -d api
# Optional social profile:
docker compose --profile social up -d linkedin-scraper
```

Or without Docker:
```bash
export PYTHONPATH=.
uvicorn backend.app_service:app --host 0.0.0.0 --port 8000
```

### Keys in pool
```bash
curl -X POST http://localhost:8000/leads/v2/keys/add \
  -H "Authorization: Bearer $LEAD_ENGINE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"serper","key_value":"YOUR_SERPER_KEY","label":"primary"}'
```

### Create first client (tenant)
```bash
curl -X POST http://localhost:8000/leads/v2/tenants \
  -H "Authorization: Bearer $LEAD_ENGINE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Agency",
    "plan": "growth",
    "leads_per_month_limit": 300,
    "delivery_channel": "telegram",
    "delivery_config": {"bot_token":"...","chat_id":"..."}
  }'
```
Then create ICP:
```bash
curl -X POST http://localhost:8000/leads/v2/icps \
  -H "Authorization: Bearer $LEAD_ENGINE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id":"TENANT_UUID",
    "vertical":"medspa",
    "geo":["Hoboken NJ","Jersey City NJ"],
    "keywords":["med spa","medical spa"],
    "exclusivity":"exclusive"
  }'
```

Seed CRM stages:
```bash
curl -X POST http://localhost:8000/crm/setup \
  -H "Authorization: Bearer $CLIENT_JWT" \
  -H "X-Tenant-Id: TENANT_UUID"
```

---

## 2. Daily / weekly operator flow

1. **Run pipeline** for a client ICP (you only):
```bash
curl -X POST http://localhost:8000/leads/v2/pipeline/run \
  -H "Authorization: Bearer $LEAD_ENGINE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id":"TENANT_UUID",
    "icp_id":"ICP_UUID",
    "industry":"medspa",
    "limit_per_location":40,
    "deliver":true
  }'
```

2. **Check ops** — `GET /leads/v2/ops/dashboard?days=7`

3. **Spot-check** 10 Tier A leads (phone/email) before first paid delivery

4. **Client works leads** in CRM portal (stages, notes, tasks)

5. **Month end** — `POST /leads/v2/billing/report/{tenant_id}` if Stripe linked

---

## 3. Pipeline stages (production path)

```
Discover (Serper Maps)
  → Pre-enrich suppression / global opt-out
  → Enrich (Scrapling → Crawl4AI → Firecrawl)
  → post_enrich_prepare (MX verify, signals, tech, priors, score v3)
  → Quality gate (Tier A/B)
  → Social enrich (LinkedIn service + Instagram, cap 25)
  → Assign (exclusivity lock)
  → Deliver (Telegram / webhook / email)
```

---

## 4. What you sell vs what client sees

| You (operator) | Client |
|----------------|--------|
| Pipeline runs, keys, ops dashboard | CRM: leads, pipeline, tasks, reports |
| Suppression uploads, opt-outs | Stage moves, notes, export CSV |
| Stripe report | Quota meter on dashboard |
| ICP / geo configuration | Delivered leads only |

---

## 5. Production checklist (service)

- [ ] All SQL migrations applied
- [ ] `LEAD_ENGINE_SERVICE_KEY` set (≥16 chars)
- [ ] `DELIVERY_CONFIG_KEY` set; delivery tokens encrypted
- [ ] Serper key in `api_key_pool`
- [ ] One live city run completed; ≥30 Tier A manually verified
- [ ] Telegram/webhook delivery tested for first tenant
- [ ] CRM stages seeded; client can open portal
- [ ] `/health` returns ok behind your reverse proxy
- [ ] `ENVIRONMENT=production` (dev auth bypass off)
- [ ] Stripe linked only after first paid agreement

---

## 6. Low-RAM VPS notes (1–2 GB)

- Run **one** pipeline at a time (`--workers 1`)
- `limit_per_location` ≤ 40
- `max_enrich_concurrent` ≤ 4
- Start LinkedIn scraper only when needed (`--profile social`)
- Prefer Scrapling/Firecrawl API over heavy local browsers when possible

## LeadX portal concurrency

- Production Docker runs FastAPI with `WEB_CONCURRENCY` workers (default `4`).
- Keep the Codespaces development command single-worker with `--reload`; do not combine reload with multiple workers.
- The auth HTTP client is pooled and supports up to 500 concurrent upstream connections per worker, avoiding one new Supabase connection per login/request.
- For a real production deployment, place the API behind a managed load balancer and scale worker processes/instances horizontally rather than relying on a single Codespaces process.
