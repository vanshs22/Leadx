# LeadX API — local default is version-pinned; production supplies an immutable digest.
ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app WORKER_HEALTH_FILE=/tmp/leadx-worker-heartbeat PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000 PLAYWRIGHT_BROWSERS_PATH=/home/leadx/.cache/ms-playwright CRAWL4_AI_BASE_DIRECTORY=/home/leadx
WORKDIR /app
COPY requirements.lock requirements.txt ./
RUN mkdir -p /home/leadx/.cache/ms-playwright \
    && python -m pip install --retries 8 --timeout 60 -r requirements.lock \
    && python -m pip install --retries 8 --timeout 60 crawl4ai==0.9.5 httpx==0.27.2 \
    && python -m playwright install --with-deps chromium chromium-headless-shell \
    && python -m pip check
COPY app ./app
COPY n8n ./n8n
COPY docs ./docs
COPY scripts ./scripts
COPY services ./services
RUN useradd --system --uid 10001 --home-dir /home/leadx --create-home leadx && mkdir -p /var/lib/leadx && chown -R leadx /app /var/lib/leadx /home/leadx
USER leadx
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 CMD ["sh", "-c", "if [ \"${LEADX_PROCESS:-api}\" = \"worker\" ]; then python scripts/check_worker_health.py; else python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)\"; fi"]
CMD if [ "${LEADX_PROCESS:-api}" = "worker" ]; then exec python -m app.worker; else exec uvicorn app.app_service:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout-keep-alive ${UVICORN_KEEPALIVE:-15} --limit-concurrency ${UVICORN_LIMIT_CONCURRENCY:-500}; fi
