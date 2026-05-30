# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# ── system deps ───────────────────────────────────────────────────────────────
# nothing heavy needed — engine uses stdlib HTTP, no Pillow/ffmpeg here yet.
# When video summaries get added back we'll layer in ffmpeg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (cached layer) ────────────────────────────────────────────────
COPY service/requirements.txt /app/service/requirements.txt
RUN pip install --no-cache-dir -r /app/service/requirements.txt

# ── source ────────────────────────────────────────────────────────────────────
COPY scripts /app/scripts
COPY service /app/service

# Fly probes /health
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--timeout-keep-alive", "120"]
