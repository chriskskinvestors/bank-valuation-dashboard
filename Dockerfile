# Base image pinned by digest (Gap B, production-readiness audit) so a base-image
# refresh can't change the Python patch / OS libs under us — same drift class as
# the dependency lock. Re-pin DELIBERATELY when upgrading:
#   docker buildx imagetools inspect python:3.11-slim   (or the Docker Hub manifest)
# python:3.11-slim, digest fetched 2026-06-17.
FROM python:3.11-slim@sha256:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca

WORKDIR /app

# curl for health checks; chromium + fonts for filing→PDF rendering
# (data/filing_pdf.py — Recent Documents "Download PDF", owner-approved
# prototype 2026-07-08). fonts-liberation covers the Times/Arial metrics
# EDGAR filings assume.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl chromium fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
ENV CHROMIUM_BIN=/usr/bin/chromium

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run as a NON-ROOT user (pre-assessment security sweep, 2026-09-16). The code
# tree stays root-owned and read-only; only the paths the service and jobs
# write at runtime are handed to the app user — inventoried from the code:
#   • every data/cloud_storage.save_json prefix (the local cache copy beside
#     each GCS write: consensus, estimates_cache, bank_groups, saved_screens,
#     form13f_cache, form4_cache, macro_cache, governance_cache, nport_cache,
#     people_cache, release_ai_cache) — consensus/estimates_cache are also
#     mkdir'd at import time;
#   • /app/tests — the verify-metrics and live-audit jobs write CSV reports.
# /tmp is world-writable (filing PDFs, NIC bulk zips). HOME is real so
# chromium (~/.pki, ~/.cache/fontconfig) and yfinance (~/.cache) can write.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/consensus /app/estimates_cache /app/bank_groups \
        /app/saved_screens /app/form13f_cache /app/form4_cache \
        /app/macro_cache /app/governance_cache /app/nport_cache \
        /app/people_cache /app/release_ai_cache \
    && chown -R app:app /app/consensus /app/estimates_cache /app/bank_groups \
        /app/saved_screens /app/form13f_cache /app/form4_cache \
        /app/macro_cache /app/governance_cache /app/nport_cache \
        /app/people_cache /app/release_ai_cache /app/tests
ENV HOME=/home/app \
    PYTHONDONTWRITEBYTECODE=1

# Cloud Run sets PORT env var (default 8080)
ENV PORT=8080

# GCS bucket for persistent storage
ENV GCS_BUCKET=ksk-bank-dashboard-data

# Health check
HEALTHCHECK CMD curl --fail http://localhost:${PORT}/_stcore/health || exit 1

# Everything after this line — the service entrypoint and every Cloud Run job
# command (which replace it) — runs unprivileged.
USER app

# Run Streamlit on the PORT Cloud Run provides
ENTRYPOINT ["sh", "-c", "streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false"]
