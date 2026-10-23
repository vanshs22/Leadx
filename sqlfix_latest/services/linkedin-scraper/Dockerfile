# Low-memory LinkedIn scraper for Linux VPS
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV CHROME_SINGLE_PROCESS=1
ENV LINKEDIN_PAGE_WAIT=2.5
ENV PORT=8001

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8001
# Single worker — one browser
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
