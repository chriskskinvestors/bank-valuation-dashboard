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

# Create writable directories for runtime data
RUN mkdir -p /app/consensus /app/estimates_cache /app/lists

# Cloud Run sets PORT env var (default 8080)
ENV PORT=8080

# GCS bucket for persistent storage
ENV GCS_BUCKET=ksk-bank-dashboard-data

# Health check
HEALTHCHECK CMD curl --fail http://localhost:${PORT}/_stcore/health || exit 1

# Run Streamlit on the PORT Cloud Run provides
ENTRYPOINT ["sh", "-c", "streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false"]
