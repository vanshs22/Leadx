# LeadX API — pinned production dependency image
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --upgrade pip==25.2 \
    && python -m pip install -r requirements.lock \
    && python -m pip check
COPY app ./app
COPY n8n ./n8n
COPY docs ./docs
COPY scripts ./scripts
COPY services ./services
RUN useradd --system --uid 10001 --no-create-home leadx && chown -R leadx /app
USER leadx
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 CMD ["sh", "-c", "if [ \"${LEADX_PROCESS:-api}\" = \"worker\" ]; then python scripts/check_worker_health.py; else curl -fsS http://127.0.0.1:8000/health/ready; fi"]
CMD if [ "${LEADX_PROCESS:-api}" = "worker" ]; then exec python -m app.worker; else exec uvicorn app.app_service:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-4} --timeout-keep-alive ${UVICORN_KEEPALIVE:-15} --limit-concurrency ${UVICORN_LIMIT_CONCURRENCY:-500}; fi
